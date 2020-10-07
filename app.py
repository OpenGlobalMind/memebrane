import json
import re
from configparser import ConfigParser

from flask import Flask, redirect, render_template, request, Response
from flask_sqlalchemy import SQLAlchemy
import requests

from models.models import Node
from models.utils import get_brain, get_node, add_brain, convert_link, LINK_RE


config = ConfigParser()
config.read('config.ini')
mbconfig = config['memebrane']

app = Flask(__name__)
app.config['FLASK_DEBUG'] = True
app.config['STATIC_FOLDER'] = '/static'
app.config['TEMPLATES_FOLDER'] = '/templates'
app.config['SQLALCHEMY_DATABASE_URI'] = mbconfig['dburl']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


@app.route("/")
def home():
    brain = get_brain(db.session, mbconfig['default_brain'])
    if not brain or not brain.base_id:
        return Response(status=404)
    return redirect(f'/brain/{brain.safe_slug}/thought/{brain.base_id}', code=302)


BRAIN_URL_RE = re.compile(
    r'https://app.thebrain.com/brains/(?P<brain_id>[^/]+)/thoughts/(?P<thought_id>[^/]+)')


@app.route("/brain/<brain_slug>")
def base_brain(brain_slug):
    brain = get_brain(db.session, brain_slug)
    if not brain:
        return Response("No such brain", status=404)
    # TODO: check if the brain really exists. Record failure in DB otherwise
    if not brain.base_id:
        return Response("No base_id", status=404)
    return redirect(f'/brain/{brain.safe_slug}/thought/{brain.base_id}', code=302)


@app.route("/brain/<brain_slug>/search")
def search(brain_slug):
    brain = get_brain(db.session, brain_slug)
    if not brain:
        return Response("No such brain", status=404)
    terms = request.args.get('query', None)
    limit = int(request.args.get('limit', 10))
    start = int(request.args.get('start', 0))
    if not terms:
        return Response("Please add a ?query parameter", status=400)
    nodes = db.session.query(Node.id, Node.name).filter(
        (Node.brain == brain) & (Node.name.match(terms))
        ).offset(start).limit(limit).all()
    prev_link = next_link = None
    if len(nodes) == limit:
        next_start = start + limit
        next_link = f"/brain/{brain_slug}/search?start={next_start}&limit={limit}&query={terms}"
    if start > 0:
        prev_start = max(0, start - limit)
        prev_link = f"/brain/{brain_slug}/search?start={prev_start}&limit={limit}&query={terms}"
    return render_template(
        "search_results.html", nodes=nodes, brain=brain, query=terms,
        start=start+1, prev_link=prev_link, next_link=next_link)


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
            brain_id, thought_id = match.group('brain_id'), match.group('thought_id')
            add_brain(db.session, brain_id, slug, name, thought_id)
            return redirect(f'/brain/{brain_id}/thought/{thought_id}', code=302)

    # no joy
    return render_template(
        'url-error.html',
        bad_url=url
    )


@app.route("/brain/<brain_slug>/thought/<thought_id>")
def get_thought_route(brain_slug, thought_id):
    brain = get_brain(db.session, brain_slug)
    if not brain:
        return Response("No such brain", status=404)

    if brain.slug and brain_slug == brain.id:
        # prefer the short form
        query_string = ('?' + request.query_string.decode('ascii')) if request.query_string else ''
        return redirect(f'/brain/{brain.slug}/thought/{thought_id}{query_string}', code=302)

    force = request.args.get('reload', False)
    node, data = get_node(db.session, brain, thought_id, force=force)
    if not node:
        return Response("No such thought", status=404)

    # get show args
    show = request.args.get('show', '')
    show_query_string = f"?show={show}" if show else ''

    linkst = dict(parent={}, child={}, sibling={}, jump={}, tag={}, of_tag={})
    tags = []
    if 'json' in show and not data:
        thoughts = [node.data]
        links = []
        for ltype, node_, link in node.get_neighbour_data(
                db.session, full=True, with_links=True):
            linkst[ltype][node_.id] = node_.name
            if ltype == 'tag':
                tags.append(node_)
            elif ltype != 'of_tag':
                thoughts.append(node_.data)
                links.append(link.data)
        root = dict(
            id=node.id,
            attachments=[att.id for att in node.attachments],  # TODO
            jumps=list(linkst['jump'].keys()),
            parents=list(linkst['parent'].keys()),
            siblings=list(linkst['sibling'].keys()),
            children=list(linkst['child'].keys()))
        data = dict(
            root=root, thoughts=thoughts, links=links,
            brainId=brain.id, isUserAuthenticated=False, errors=[], stamp=0,
            status=1, tags=tags, notesHtml="", notesMarkdown="")
    else:
        if not data:
            # TODO: Store in node
            root = dict(attachments=[att.id for att in node.attachments])
            data = dict(root=root, notesHtml="", notesMarkdown="", tags=[])
        for (ltype, id, name) in node.get_neighbour_data(db.session):
            linkst[ltype][id] = name

    # create a lookup table of names by thought_id
    names = {node.id: node.name}
    for d in linkst.values():
        names.update(d)

    notes_html = node.data.get('notesHtml', "")
    notes_html = re.sub(LINK_RE, lambda match: convert_link(match, brain, show_query_string), notes_html)
    # render page
    return render_template(
        'index.html',
        json=json.dumps(data, indent=2),
        show=show,
        show_query_string=show_query_string,
        brain=brain,
        node=node.data,
        tags=linkst['tag'],
        parents=linkst['parent'],
        siblings=linkst['sibling'],
        children=linkst['child'],
        jumps=linkst['jump'],
        of_tag=linkst['of_tag'],
        names=names,
        attachments=node.attachments,
        notes_html=notes_html,
        notes_markdown=node.data.get('notesMarkdown', ""),
    )
