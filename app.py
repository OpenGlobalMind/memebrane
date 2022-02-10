import simplejson as json
import re
from mimetypes import guess_type
from io import StringIO
import csv
import os

from flask import Flask, redirect, render_template, request, Response, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
import requests
from sqlalchemy.orm import undefer, aliased
from sqlalchemy.sql import func

from models import mbconfig, text_index_langs, postgres_language_configurations
from models.models import Node, Brain, Link, Attachment, as_reg_class
from models.utils import (
    get_brain, get_node, add_brain, convert_link, LINK_RE, process_markdown,
    resolve_html_links)



app = Flask(__name__)
app.config['STATIC_FOLDER'] = '/static'
app.config['TEMPLATES_FOLDER'] = '/templates'
app.config['SQLALCHEMY_DATABASE_URI'] = mbconfig['dburl']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
if os.environ.get('FLASK_ENV') == 'development':
    app.config['FLASK_DEBUG'] = True
    app.config['TESTING'] = True

db = SQLAlchemy(app)
CORS(app)

@app.route("/")
def home():
    brain = get_brain(db.session, mbconfig['default_brain'])
    if not brain or not brain.base_id:
        return Response(status=404)
    return redirect(f'/brain/{brain.safe_slug}/thought/{brain.base_id}', code=302)


BRAIN_URL_RE = re.compile(
    r'https://app.thebrain.com/brains/(?P<brain_id>[^/]+)/thoughts/(?P<thought_id>[^/]+)')


@app.route("/brain")
def list_brains():
    brains = db.session.query(Brain).all()
    return render_template(
        "list_brains.html", brains=brains)


@app.route("/brain/<brain_slug>")
def base_brain(brain_slug):
    brain = get_brain(db.session, brain_slug)
    if not brain:
        return Response("No such brain", status=404)
    # TODO: check if the brain really exists. Record failure in DB otherwise
    if request.accept_mimetypes.best != 'application/json':
        node_id = brain.base_id or brain.top_node_id(db)
        return redirect(f'/brain/{brain.safe_slug}/thought/{node_id}', code=302)
    nodes = db.session.query(Node.data).filter_by(brain_id=brain.id, private=False).all()
    n1 = aliased(Node)
    n2 = aliased(Node)
    links = db.session.query(Link.data).filter_by(brain_id=brain.id
        ).join(n1, (Link.parent_id==n1.id) & (n1.private==False)
        ).join(n2, (Link.child_id==n2.id) & (n2.private==False)).all()
    attachments = db.session.query(
        Attachment.data).join(Node).filter_by(brain_id=brain.id, private=False).all()
    return dict(nodes=nodes, links=links, attachments=attachments)


@app.route("/brain/<brain_slug>/search")
def search(brain_slug):
    brain = get_brain(db.session, brain_slug)
    if not brain:
        return Response("No such brain", status=404)
    terms = request.args.get('query', None)
    limit = int(request.args.get('limit', 10))
    start = int(request.args.get('start', 0))
    if not terms:
        return render_template(
            "search.html",
            brain_name=brain.name,
            langs = {lang: postgres_language_configurations[lang]
                     for lang in text_index_langs}
        )
    lang = request.args.get('lang', None)
    pglang = 'simple'
    if lang in text_index_langs:
        pglang = postgres_language_configurations.get(lang, 'simple')
    txtarg1 = func.to_tsvector(as_reg_class(pglang), Node.name)
    filter = txtarg1.op('@@')(func.websearch_to_tsquery(as_reg_class(pglang), terms))
    rank = func.ts_rank(txtarg1, func.websearch_to_tsquery(as_reg_class(pglang), terms), 1)
    query = db.session.query(Node.id, Node.name).filter_by(brain=brain, private=False)
    use_notes = request.args.get('notes', None)
    use_notes = use_notes and use_notes.lower() in ['true', 'on', 'checked', 'yes']
    if use_notes:
        query = query.outerjoin(Attachment)
        txtarg2 = func.to_tsvector(as_reg_class(pglang), Attachment.text_content)
        tfilter = txtarg2.op('@@')(func.websearch_to_tsquery(as_reg_class(pglang), terms))
        rank = func.coalesce(rank, 0) + 2 * func.coalesce(func.ts_rank_cd(txtarg2, func.websearch_to_tsquery(as_reg_class(pglang), terms), 1), 0)
        if lang:
            filter = filter | ((Attachment.inferred_locale == lang) & tfilter)
        else:
            filter = filter | tfilter
    nodes = query.filter(filter).order_by(rank.desc()).offset(start).limit(limit).all()
    prev_link = next_link = None
    if len(nodes) == limit:
        next_start = start + limit
        next_link = f"/brain/{brain_slug}/search?start={next_start}&limit={limit}&query={terms}"
        if use_notes:
            next_link += "&notes=true"
        if lang:
            next_link += "&lang=" + lang
    if start > 0:
        prev_start = max(0, start - limit)
        prev_link = f"/brain/{brain_slug}/search?start={prev_start}&limit={limit}&query={terms}"
        if use_notes:
            prev_link += "&notes=true"
        if lang:
            prev_link += "&lang=" + lang
    mimetype = request.args.get("mimetype", request.accept_mimetypes.best)
    if mimetype == 'application/json':
        return dict(start=start+1, end=start+len(nodes), limit=limit, lang=lang,
            notes=use_notes, results={n.id: n.name for n in nodes}
        )

    return render_template(
        "search_results.html", nodes=nodes, brain=brain, query=terms,
        start=start+1, end=start+len(nodes), prev_link=prev_link, next_link=next_link)


@app.route("/url", methods=['POST'])
def url():
    url = request.form['url']
    slug = request.form.get('slug', None)
    name = request.form.get('name', None)

    # TODO: support URLs of the form https://webbrain.com/brainpage/brain/BED8187E-FD1C-CE55-E236-871DD7E1DF32#-6975

    # resolve shortened URL
    if url.startswith('https://bra.in/'):
        r = requests.get(url)
        url = r.url

    # check for reasonable URL
    if url.startswith('https://app.thebrain.com/'):
        match = BRAIN_URL_RE.match(url)
        if match is not None:
            brain_id, thought_id = match.group(
                'brain_id'), match.group('thought_id')
            add_brain(db.session, brain_id, slug, name, thought_id)
            return redirect(f'/brain/{brain_id}/thought/{thought_id}', code=302)

    # no joy
    return render_template(
        'url-error.html',
        bad_url=url
    )


def recompose_data(node):
    linkst = dict(parent={}, child={}, sibling={}, jump={}, tag={}, of_tag={})
    thoughts = [node.data]
    links = []
    tags = []
    for ltype, node_, link in node.get_neighbour_data(
            db.session, full=True, with_links=True):
        linkst[ltype][node_.id] = node_.name
        if ltype == 'tag':
            tags.append(node_)
        elif ltype != 'of_tag':
            thoughts.append(node_.data)
            links.append(link.data)
    attachments = [dict(
        id=att.id, location=att.location_adjusted, type=att.att_type.name, name=att.name,
        last_modified=att.last_modified.isoformat() if att.last_modified else None)
        for att in node.attachments]
    root = dict(
        id=node.id,
        attachments=attachments,
        jumps=list(linkst['jump'].keys()),
        parents=list(linkst['parent'].keys()),
        siblings=list(linkst['sibling'].keys()),
        children=list(linkst['child'].keys()))
    return linkst, dict(
        root=root, thoughts=thoughts, links=links,
        brainId=node.brain.id, isUserAuthenticated=False, errors=[], stamp=0,
        status=1, tags=[tag.data for tag in tags], notesHtml="", notesMarkdown="")


@app.route("/brain/<brain_slug>/thought/<thought_id>/")
def get_thought_route(brain_slug, thought_id):
    brain = get_brain(db.session, brain_slug)
    if not brain:
        return Response("No such brain", status=404)

    if brain.slug and brain_slug == brain.id:
        # prefer the short form
        query_string = ('?' + request.query_string.decode('ascii')
                        ) if request.query_string else ''
        return redirect(f'/brain/{brain.slug}/thought/{thought_id}/{query_string}', code=302)

    force = request.args.get('reload', False)
    node, data = get_node(db.session, brain, thought_id, force=force)
    if not node:
        return Response("No such thought", status=404)

    if node.private:
        # TODO: Give the brain link
        return Response("Private thought", status=403)

    mimetype = request.args.get("mimetype", request.accept_mimetypes.best)
    if mimetype == 'application/json':
        return data or recompose_data(node)[1]
    elif mimetype == 'text/csv':
        neighbours = list(node.get_neighbour_data(
                db.session, full=True, text_links=True, text_backlinks=True, with_links=True, with_attachments=True))
        reread = False
        for rel, node2, link in neighbours:
            if not node2.read_as_focus:
                node2 = get_node(db.session, brain, node2.id, force=True)
                reread = True
        if reread:
            neighbours = node.get_neighbour_data(
                db.session, full=True, text_links=True, text_backlinks=True, with_links=True, with_attachments=True)
        si = StringIO()
        cw = csv.writer(si)
        cw.writerow(["Name", "Node_UUID", "Node_Type", "URL", "Notes", "Link_Type", "Link_UUID"])
        cw.writerow([node.name, node.id, node.type_name, node.url_link(), node.get_notes_as_md(), "self", ""])
        for rel, node2, link in neighbours:
            cw.writerow([node2.name, node2.id, node2.type_name, node2.url_link(),
                node2.get_notes_as_md(), rel, link.id if link else ""])

        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = "attachment; filename=export.csv"
        output.headers["Content-type"] = "text/csv"
        return output

    # get show args
    show = request.args.get('show', '')
    show_query_string = f"?show={show}" if show else ''

    if 'json' in show and not data:
        linkst, data = recompose_data(node)
    else:
        if not data:
            # TODO: Store in node
            root = dict(attachments=[att.id for att in node.attachments])
            data = dict(root=root, notesHtml="", notesMarkdown="", tags=[])
        linkst = dict(parent={}, child={}, sibling={},
                      jump={}, tag={}, of_tag={})
        for (ltype, id, name) in node.get_neighbour_data(
                db.session, siblings='siblings' in show):
            linkst[ltype][id] = name

    # create a lookup table of names by thought_id
    names = {node.id: node.name}
    for d in linkst.values():
        names.update(d)

    notes_html = node.get_notes_as_html()
    if notes_html:
        notes_html = re.sub(
            LINK_RE,
            lambda match: convert_link(match, brain, show_query_string),
            notes_html)
        notes_html = resolve_html_links(notes_html)

    # render page
    return render_template(
        'index.html',
        json=json.dumps(data, indent=2),
        show=show,
        show_query_string=show_query_string,
        brain=brain,
        node=node.data,
        is_tag=node.is_tag,
        is_type=node.is_type,
        tags=linkst['tag'],
        parents=linkst['parent'],
        siblings=linkst['sibling'],
        children=linkst['child'],
        jumps=linkst['jump'],
        of_tag=linkst['of_tag'],
        names=names,
        attachments=node.attachments,
        notes_html=notes_html,
    )


@app.route("/brain/<brain_slug>/thought/<thought_id>/.data/md-images/<location>")
def get_image_content(brain_slug, thought_id, location):
    brain = get_brain(db.session, brain_slug)
    if not brain:
        return Response("No such brain", status=404)
    # TODO: use node ID implicit in location?
    # Honour the $width=100p$ parameter
    att = db.session.query(Attachment).filter_by(
        brain=brain,
        node_id=thought_id,
        location=location
    ).options(undefer(Attachment.content)).first()
    # TODO: handle duplicate notes.html.
    # May differ in noteType, but no clear interpretation.

    if not att:
        node, data = get_node(db.session, brain, thought_id)
        if not node:
            return Response("No such node", status=404)

        if node.private:
            return Response("Private thought", status=403)

        atts = [a for a in node.attachments if a.location == location]
        if not atts:
            return Response("No such image", status=404)
        att = atts[0]
    att.populate_content()
    content = att.text_content or att.content
    if not content:
        # maybe a permission issue? redirect to brain
        return Response(headers={"location":att.brain_uri()}, status=303)
    # TODO: Use /etc/nginx/mime.types, which is fuller, but strip semicolons
    return Response(content, mimetype=guess_type(location, False)[0])
