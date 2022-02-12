from configparser import ConfigParser
from os.path import join, dirname
import simplejson as json
from datetime import timedelta, datetime
import re
import base64
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import requests
from markdown import markdown

from . import BRAIN_API
from .models import AttachmentType, Node, Brain, Link, Attachment

CONFIG_BRAINS = None


def get_thought_data(brain_id, thought_id, graph=True):
    base = f"https://api.thebrain.com/{BRAIN_API}/brains/{brain_id}/thoughts/{thought_id}"
    if graph:
        r = requests.get(base + "/graph")
        if r.ok:
            try:
                return r.json()
            except Exception as e:
                pass
    else:
        r1 = requests.get(base + "/note")
        if not r1.ok:
            return None
        r2 = requests.get(base)
        if not r2.ok:
            return None
        try:
            r1 = r1.json()
            r2 = r2.json()
        except Exception as e:
            return None
        return {
            # TODO
        }

def get_config_brains():
    global CONFIG_BRAINS
    if CONFIG_BRAINS is None:
        with open(join(dirname(dirname(__file__)), 'brains.json')) as f:
            CONFIG_BRAINS = json.load(f)
    return CONFIG_BRAINS


UUID_S = \
    r'[0-9a-f]{8}\-[0-9a-f]{4}\-[0-9a-f]{4}\-[0-9a-f]{4}\-[0-9a-f]{12}'
UUID_B64 = r'[-_A-Za-z0-9]{22}'
LINK_RE = re.compile(
    rf'\bbrain://(?:api\.thebrain\.com/(?P<brain>{UUID_B64})/)?(?P<node>{UUID_B64})/(?P<suffix>\w+)\b')
UUID_RE = re.compile(rf'^{UUID_S}$', re.I)
BRAIN_BASE1_S = r"<!--BrainNotesBase-->"
BRAIN_BASE2_S = r".data/md-images"
BRAIN_BASE1_RE = re.compile(BRAIN_BASE1_S)


def _b642uuid(b):
    b = base64.urlsafe_b64decode(b+'==')
    return uuid.UUID(bytes_le=b)


def extract_link(link_match):
    re_g = link_match.groupdict()
    brain_s = re_g['brain']
    return (_b642uuid(re_g['node']), _b642uuid(brain_s) if brain_s else None)


def convert_link(link_match, brain, query_string=''):
    node_id, brain_id = extract_link(link_match)
    if brain_id:
        return f"/brain/{brain_id}/thought/{node_id}/{query_string}"
    else:
        return f"/brain/{brain.safe_slug}/thought/{node_id}/{query_string}"

def extract_text_links(text, brain_id):
    acc = []
    if text:
        for m in LINK_RE.finditer(text):
            n_id, b_id = extract_link(m)
            if b_id is None or b_id == brain_id:
                acc.append(str(n_id))
    return acc


def extract_text_links_from_data(data):
    brain_id = data["brainId"]
    return extract_text_links(data.get("notesHtml", None), brain_id) \
        or extract_text_links(data.get("notesMarkdown", None), brain_id)


def get_brain(session, slug):
    global CONFIG_BRAINS
    get_config_brains()
    if UUID_RE.match(slug):
        brain_data = [b for b in CONFIG_BRAINS.values()
                        if b['brain'] == slug]
        brain = session.query(Brain).filter_by(id=slug).first()
        if not brain:
            if brain_data:
                brain_data = brain_data[0]
                brain = add_brain(session, brain_data['brain'], slug,
                                    brain_data['name'], brain_data.get('thought', None))
            else:
                brain = add_brain(session, slug)
    else:
        brain = session.query(Brain).filter_by(slug=slug).first()
        brain_data = CONFIG_BRAINS.get(slug, None)
        if brain_data and not brain:
            brain = add_brain(session, brain_data['brain'], slug,
                                brain_data['name'], brain_data.get('thought', None))
    return brain


def add_brain(session, id, slug=None, name=None, base_id=None):
    brain = Brain(id=id, name=name, base_id=base_id, slug=slug)
    session.add(brain)
    session.commit()
    return brain


def get_engine(password='memebrane', user='memebrane', host='localhost', db='memebrane', port=5432):
    # TODO eliminate
    return create_engine(f'postgresql://{user}:{password}@{host}:{port}/{db}')


def engine_from_config():
    config = ConfigParser()
    config.read('config.ini')
    return create_engine(config['memebrane']['dburl'])


def get_session(engine=None):
    engine = engine or engine_from_config()
    Session = sessionmaker(bind=engine)
    return Session()


def populate_brains(session, brains):
    for slug, brain_def in brains.items():
        brain = Brain(id=brain_def['brain'], name=brain_def['name'],
                      base_id=brain_def['thought'], slug=slug)
        session.add(brain)


def add_to_cache(session, brain_id, data, force=False, graph=True):
    root_id = data['root']['id']
    nodes = {t['id']: t for t in data["thoughts"]}
    nodes.update({t['id']: t for t in data["tags"]})
    node_ids = set(nodes.keys())
    nodes_in_cache = session.query(Node).filter(
        Node.id.in_(nodes.keys()), Node.brain_id==brain_id)
    for node in nodes_in_cache:
        node_data = nodes.pop(node.id)
        focus = node.id == root_id
        if focus and 'tags' in data:
            node_data['tags'] = data.get('tags', [])
        node.update_from_json(node_data, focus, force)
    for node_data in nodes.values():
        focus = node_data['id'] == root_id
        if focus:
            node_data['tags'] = data.get('tags', [])
        session.add(Node.create_from_json(
            node_data, node_data['id'] == root_id))
    session.flush()
    links = {l['id']: l for l in data.get("links", ())}
    links_in_cache = session.query(Link).filter(
        Link.id.in_(links.keys()), Link.brain_id==brain_id)
    for link in links_in_cache:
        link_data = links.pop(link.id, None)
        if link_data:
            link.update_from_json(link_data, force)
    for ldata in links.values():
        if ldata['thoughtIdA'] in node_ids and ldata['thoughtIdB'] in node_ids:
            session.add(Link.create_from_json(ldata))
        else:
            print(f"Missing node for this link:{ldata}")
    attachments = {l['id']: l for l in data.get("attachments", ())}
    attachments_in_cache = session.query(Attachment).filter(
        Attachment.id.in_(attachments.keys()))
    def get_content(adata, data):
        content = None
        atype = adata.get("type", 0)
        if atype == AttachmentType.NotesV9.value:
            return convert_api_links(data["notesHtml"], root_id, brain_id)
        elif atype == AttachmentType.InternalFile.value and adata.get("noteType", 0) == 4:
            return convert_api_links(data["notesMarkdown"], root_id, brain_id)
        else:
            # TODO: Should I get the attachment content from the link?
            pass

    for attachment in attachments_in_cache:
        adata = attachments.pop(attachment.id, None)
        if adata:
            attachment.update_from_json(
                adata, get_content(adata, data), force=force)
    # TODO: Should I delete absent attachments? only if graph of course
    for adata in attachments.values():
        session.add(Attachment.create_from_json(adata, get_content(adata, data)))
    session.commit()


def get_node(session, brain, id, cache_staleness=timedelta(days=1), force=False, graph=True):
    node = session.query(Node).filter_by(id=id, brain_id=brain.id).first()
    data = None
    if force or not node or cache_staleness is None or not node.read_as_focus or datetime.now() - node.last_read > cache_staleness:
        data = get_thought_data(brain.id, id, graph)
        if data:
            add_to_cache(session, brain.id, data, force, graph)
            if not node:
                node = session.query(Node).filter_by(
                    id=id, brain_id=brain.id).first()
    return node, data


def create_tables(engine):
    with engine.connect() as conn:
        Node.metadata.create_all(conn)


def lcase1(str):
    if str[1].lower() == str[1]:
        return str[0].lower() + str[1:]
    return str


def lcase_json(json):
    return {
        lcase1(key): lcase_json(val) if isinstance(val, dict) else val
        for key, val in json.items()
    }


def convert_api_links(text, node_id, brain_id):
    image_re = re.compile(
        rf'https://api.thebrain.com/{BRAIN_API}/brains/{brain_id}/thoughts/{node_id}/md-images/({UUID_S}\.\w+)')
    return image_re.sub(r".data/md-images/\1", text)

def resolve_html_links(html):
    return BRAIN_BASE1_RE.sub(BRAIN_BASE2_S, html)

def process_markdown(md):
    md = BRAIN_BASE1_RE.sub(BRAIN_BASE2_S, md)
    return markdown(md)


def html_to_markdown(html, flavour="markdown"):
    try:
        import pypandoc
        return pypandoc.convert(html, flavour, "html")
    except ImportError:
        pass


if __name__ == '__main__':
    engine = get_engine()
    create_tables(engine)
    populate_brains(get_session(engine), get_config_brains())
