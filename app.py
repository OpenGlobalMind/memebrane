import simplejson as json
import re
from mimetypes import guess_type
from io import StringIO
import csv
import os
from itertools import groupby
from datetime import timedelta

from quart import Quart, redirect, render_template, request, Response, make_response
from sqlalchemy.future import select
from quart_cors import cors
from sqlalchemy.orm import undefer, aliased

from models import mbconfig, text_index_langs, postgres_language_configurations
from models.models import Node, Brain, Link, Attachment, AttachmentType
from models.utils import (
    get_brain, get_node, add_brain, convert_link, LINK_RE, get_session_maker,
    resolve_html_links, httpx_client)



app = Quart(__name__)
app.config['STATIC_FOLDER'] = '/static'
app.config['TEMPLATES_FOLDER'] = '/templates'
app.config['SQLALCHEMY_DATABASE_URI'] = mbconfig['dburl']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
if os.environ.get('QUART_ENV') == 'development':
    app.config['QUART_DEBUG'] = True
    app.config['TESTING'] = True


class SQLAMiddleware:

    def __init__(self, app):
        self.app = app
        self.sessions = get_session_maker(expire_on_commit=False)

    async def __call__(self, scope, receive, send):
        session = self.sessions()
        scope['session'] = session
        try:
            return await self.app(scope, receive, send)
        except Exception as e:
            await session.rollback()
            raise e from e
        finally:
            await session.close()

app.asgi_app = SQLAMiddleware(app.asgi_app)
cors(app)

@app.route("/")
async def home():
    session = request.scope['session']
    brain = await get_brain(session, mbconfig['default_brain'])
    if not brain or not brain.base_id:
        return Response(status=404)
    return redirect(f'/brain/{brain.safe_slug}/thought/{brain.base_id}', code=302)


BRAIN_URL_RE = re.compile(
    r'https://app.thebrain.com/brains/(?P<brain_id>[^/]+)/thoughts/(?P<thought_id>[^/]+)')


@app.route("/brain")
async def list_brains():
    session = request.scope['session']
    brains = await session.execute(select(Brain))
    brains = [x for (x,) in brains]
    return await render_template(
        "list_brains.html", brains=brains)


@app.route("/brain/<brain_slug>")
async def base_brain(brain_slug):
    session = request.scope['session']
    brain = await get_brain(session, brain_slug)
    if not brain:
        return Response("No such brain", status=404)
    # TODO: check if the brain really exists. Record failure in DB otherwise
    if request.accept_mimetypes.best != 'application/json':
        node_id = brain.base_id or brain.top_node_id(session)
        return redirect(f'/brain/{brain.safe_slug}/thought/{node_id}', code=302)
    nodes = await session.execute(select(Node.data).filter_by(brain_id=brain.id, private=False))
    nodes = [node for (node,) in nodes]
    n1 = aliased(Node)
    n2 = aliased(Node)
    links = await session.execute(select(Link.data).filter_by(brain_id=brain.id
        ).join(n1, (Link.parent_id==n1.id) & (n1.private==False)
        ).join(n2, (Link.child_id==n2.id) & (n2.private==False)))
    links = [link for (link,) in links]
    attachments = await session.execute(select(
        Attachment.data).join(Node).filter_by(brain_id=brain.id, private=False))
    attachments = [attachment for (attachment,) in attachments]
    return dict(nodes=nodes, links=links, attachments=attachments)


@app.route("/brain/<brain_slug>/search")
async def search(brain_slug):
    session = request.scope['session']
    brain = await get_brain(session, brain_slug)
    if not brain:
        return Response("No such brain", status=404)
    terms = request.args.get('query', None)
    limit = int(request.args.get('limit', 10))
    start = int(request.args.get('start', 0))
    if not terms:
        return await render_template(
            "search.html",
            brain_name=brain.name,
            langs = {lang: postgres_language_configurations[lang]
                    for lang in text_index_langs}
        )
    lang = request.args.get('lang', None)
    use_notes = request.args.get('notes', None)
    use_notes = use_notes and use_notes.lower() in ['true', 'on', 'checked', 'yes']
    nodes = await Node.search(session, brain, terms, start, limit, lang, use_notes)
    nodes = list(nodes)
    prev_link = next_link = None
    if len(nodes) == limit:
        next_start = start + limit
        next_link = f"/brain/{brain_slug}/search?start={next_start}&limit={limit}&query={terms}"
        if use_notes:
            next_link += "&notes=true"
        if lang:
            next_link += f"&lang={lang}"
    if start > 0:
        prev_start = max(0, start - limit)
        prev_link = f"/brain/{brain_slug}/search?start={prev_start}&limit={limit}&query={terms}"
        if use_notes:
            prev_link += "&notes=true"
        if lang:
            prev_link += f"&lang={lang}"
    mimetype = request.args.get("mimetype", request.accept_mimetypes.best)
    if mimetype == 'application/json':
        return dict(start=start+1, end=start+len(nodes), limit=limit, lang=lang,
            notes=use_notes, results={n.id: n.name for n in nodes}
        )

    return await render_template(
        "search_results.html", nodes=nodes, brain=brain, query=terms,
        start=start+1, end=start+len(nodes), prev_link=prev_link, next_link=next_link)


@app.route("/url", methods=['POST'])
async def url():
    form = await request.form
    url = form['url']
    slug = form.get('slug', None)
    name = form.get('name', None)

    # TODO: support URLs of the form https://webbrain.com/brainpage/brain/BED8187E-FD1C-CE55-E236-871DD7E1DF32#-6975

    # resolve shortened URL
    if url.startswith('https://bra.in/'):
        r = await httpx_client.get(url)
        url = r.url

    # check for reasonable URL
    if url.startswith('https://app.thebrain.com/'):
        match = BRAIN_URL_RE.match(url)
        if match is not None:
            session = request.scope['session']
            brain_id, thought_id = match.group(
                'brain_id'), match.group('thought_id')
            await add_brain(session, brain_id, slug, name, thought_id)
            return redirect(f'/brain/{brain_id}/thought/{thought_id}', code=302)

    # no joy
    return await render_template(
        'url-error.html',
        bad_url=url
    )


async def recompose_data(node, with_attachments=False, siblings=True, gate_counts=False, **kwargs):
    linkst = dict(parent={}, child={}, sibling={}, jump={}, tag={}, of_tag={})
    thoughts = [node.data]
    links = []
    tags = []
    session = request.scope['session']
    for ltype, node_, link in await node.get_neighbour_data(
            session, True, True,
            with_attachments=with_attachments, siblings=siblings):
        linkst[ltype][node_.id] = node_.name
        data = dict(node_.data)
        if with_attachments:
            if node_.html_attachments:
                data['notesHtml'] = node_.html_attachments[0].text_content
            if node_.md_attachments:
                data['notesMarkdown'] = node_.md_attachments[0].text_content
            if node_.url_link_attachments:
                data['attachments'] = [dict(
                    id=att.id, location=att.location_adjusted, type=att.att_type.name, name=att.name,
                    last_modified=att.last_modified.isoformat() if att.last_modified else None)
                    for att in node_.url_link_attachments]
        if ltype == 'tag':
            tags.append(node_)
        elif ltype != 'of_tag':
            thoughts.append(data)
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
    data = dict(
        root=root, thoughts=thoughts, links=links,
        brainId=node.brain.id, isUserAuthenticated=False, errors=[], stamp=0,
        status=1, tags=[tag.data for tag in tags])
    if node.html_attachments:
        data['notesHtml'] = node.html_attachments[0].text_content
    if node.md_attachments:
        data['notesMarkdown'] = node.md_attachments[0].text_content
    if gate_counts:
        data['gateCounts'] = await node.gate_counts(session)
    return linkst, data


show_args = {
    'json', 'gate_counts', 'siblings', 'parents', 'children',
    'jumps', 'tags', 'of_tags', 'text_links',
    'text_backlinks', 'with_attachments', 'same_type'}
show_defaults = {'parents', 'children', 'siblings', 'jumps', 'tags', 'of_tags'}
show_defaults = {arg: arg in show_defaults for arg in show_args}
show_data_defaults = {'text_links', 'text_backlinks', 'with_attachments'}
show_data_defaults = {arg: arg in show_data_defaults for arg in show_args}

@app.route("/brain/<brain_slug>/thought/<thought_id>/")
async def get_thought_route(brain_slug, thought_id):
    session = request.scope['session']
    brain = await get_brain(session, brain_slug)
    if not brain:
        return Response("No such brain", status=404)

    if brain.slug and brain_slug == brain.id:
        # prefer the short form
        query_string = ('?' + request.query_string.decode('ascii')
                        ) if request.query_string else ''
        return redirect(f'/brain/{brain.slug}/thought/{thought_id}/{query_string}', code=302)

    mimetype = request.args.get("mimetype", request.accept_mimetypes.best)
    # query args
    show = request.args.get('show', '')
    show_list = set(show.split(','))
    my_show_defaults = show_defaults.copy()
    if mimetype in ('application/json', 'text/csv'):
        my_show_defaults.update(show_data_defaults)
    show_vals = my_show_defaults.copy()
    # accept arguments of the form: show_arg=true/false
    show_vals.update({arg: val not in ('false', '0', 'no', 'off') for (arg, val) in request.args.items() if arg in show_list})
    # accept arguments of the form: show=arg,-arg,...
    show_vals.update({arg.strip('-'): arg[0] != '-' for arg in show_defaults.keys() if arg.strip('-') in show_list})
    non_default = {arg: val for (arg, val) in show_vals.items() if val != my_show_defaults[arg]}
    show_query_string = "?show=" + ",".join([('' if val else '-')+arg for (arg, val) in non_default.items()])
    show_json = show_vals.pop('json')
    show_vals.pop('gate_counts')

    force = request.args.get('reload', False)
    # add cache_staleness and siblings to query string
    cache_staleness = request.args.get('cache_staleness', '1')
    try:
        cache_staleness = int(cache_staleness)
    except:
        cache_staleness = 1
    if cache_staleness == 0:
        force = True
    else:
        cache_staleness = timedelta(days=cache_staleness) if cache_staleness > 0 else None
    node, data = await get_node(session, brain, thought_id, force=force, cache_staleness=cache_staleness)
    if not node:
        return Response("No such thought", status=404)

    if node.private:
        # TODO: Give the brain link
        return Response("Private thought", status=403)

    if mimetype == 'application/json':
        if data and show_vals['with_attachments']:
            node_ids = [data['root']['id']]+[node['id'] for node in data['thoughts']]
            links = await session.execute(select(Attachment.where(Attachment.node_id.in_(node_ids)), Attachment.att_type==AttachmentType.ExternalUrl).order_by(Attachment.node_id))
            links_by_id = groupby(links, lambda l: l.node_id)
            for node in data['thoughts']:
                if node['id'] in links_by_id:
                    node['attachments'] = [l.data for l in links_by_id[node['id']]]
        return data or (await recompose_data(node, **show_vals))[1]
    elif mimetype == 'text/csv':
        neighbours = list(await node.get_neighbour_data(session, True, True, **show_vals))
        reread = False
        for rel, node2, link in neighbours:
            if not node2.read_as_focus:
                node2 = await get_node(session, brain, node2.id, force=True)
                reread = True
        if reread:
            neighbours = await node.get_neighbour_data(session, True, True, **show_vals)
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

    if show_json and not data:
        linkst, data = await recompose_data(node, **show_vals)
    else:
        if not data:
            # TODO: Store in node
            root = dict(attachments=[att.id for att in node.attachments])
            data = dict(root=root, notesHtml="", notesMarkdown="", tags=[])
        linkst = dict(parent={}, child={}, sibling={},
                    jump={}, tag={}, of_tag={}, same_type={})
        for (ltype, id, name) in await node.get_neighbour_data(session, **show_vals):
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
    return await render_template(
        'index.html',
        json=json.dumps(data, indent=2),
        show_vals=show_vals,
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
        same_type=linkst['same_type'],
        names=names,
        attachments=node.attachments,
        notes_html=notes_html,
    )


@app.route("/brain/<brain_slug>/thought/<thought_id>/.data/md-images/<location>")
async def get_image_content(brain_slug, thought_id, location):
    session = request.scope['session']
    brain = await get_brain(session, brain_slug)
    if not brain:
        return Response("No such brain", status=404)
    # TODO: use node ID implicit in location?
    # Honour the $width=100p$ parameter
    att = await session.scalar(select(Attachment).filter_by(
        brain=brain,
        node_id=thought_id,
        location=location
    ).options(undefer(Attachment.content)))
    # TODO: handle duplicate notes.html.
    # May differ in noteType, but no clear interpretation.

    if not att:
        node, data = await get_node(session, brain, thought_id)
        if not node:
            return Response("No such node", status=404)

        if node.private:
            return Response("Private thought", status=403)

        atts = [a for a in node.attachments if a.location == location]
        if not atts:
            return Response("No such image", status=404)
        att = atts[0]
    await att.populate_content(httpx_client)
    content = att.text_content or att.content
    if not content:
        # maybe a permission issue? redirect to brain
        return Response(headers={"location":att.brain_uri()}, status=303)
    # TODO: Use /etc/nginx/mime.types, which is fuller, but strip semicolons
    return Response(content, mimetype=guess_type(location, False)[0])


@app.route("/brain/<brain_slug>/thought/<thought_id>/notes")
async def get_notes(brain_slug, thought_id):
    session = request.scope['session']
    brain = await get_brain(session, brain_slug)
    if not brain:
        return Response("No such brain", status=404)
    node, data = await get_node(session, brain, thought_id, force=False)
    return Response(node.get_notes_as_html())
