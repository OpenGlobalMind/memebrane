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

from .models import Node, Brain, Link, Attachment

CONFIG_BRAINS = None
BRAINS = {}


def get_thought_data(brain_id, thought_id):
    print(thought_id)
    r = requests.get('https://api.thebrain.com/api-v11/brains/' +
                     brain_id + '/thoughts/' + thought_id + '/graph')
    try:
        return r.json()
    except Exception as e:
        return None


def get_config_brains():
    global CONFIG_BRAINS
    if CONFIG_BRAINS is None:
        with open(join(dirname(dirname(__file__)), 'brains.json')) as f:
            CONFIG_BRAINS = json.load(f)
    return CONFIG_BRAINS


LINK_RE = re.compile(
    r'\bbrain://(?:api\.thebrain\.com/(?P<brain>[-_A-Za-z0-9]{22})/)?(?P<node>[-_A-Za-z0-9]{22})/(?P<suffix>\w+)\b')
UUID_RE = re.compile(
    r'^[0-9a-f]{8}\-[0-9a-f]{4}\-[0-9a-f]{4}\-[0-9a-f]{4}\-[0-9a-f]{12}$', re.I)


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
        return f"/brain/{brain_id}/thought/{node_id}{query_string}"
    else:
        return f"/brain/{brain.safe_slug}/thought/{node_id}{query_string}"


def get_brain(session, slug):
    global CONFIG_BRAINS, BRAINS
    get_config_brains()
    if UUID_RE.match(slug):
        brains = [b for b in BRAINS.values() if b.id == slug]
        if brains:
            brain = brains[0]
            session.merge(brain)
        else:
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
            BRAINS[slug] = brain  # may be None, cache that
    else:
        brain = BRAINS.get(slug, None)
        if brain:
            session.merge(brain)
        else:
            brain = session.query(Brain).filter_by(slug=slug).first()
            brain_data = CONFIG_BRAINS.get(slug, None)
            if brain_data and not brain:
                brain = add_brain(session, brain_data['brain'], slug,
                                  brain_data['name'], brain_data.get('thought', None))
            BRAINS[slug] = brain  # may be None, cache that
    return brain


def add_brain(session, id, slug=None, name=None, base_id=None):
    global BRAINS
    brain = Brain(id=id, name=name, base_id=base_id, slug=slug)
    session.add(brain)
    session.commit()
    BRAINS[slug] = brain
    return brain


def get_engine(password='memebrane', user='memebrane', host='localhost', db='memebrane', port=5432):
    # TODO eliminate
    return create_engine(f'postgresql://{user}:{password}@{host}:{port}/{db}')


def engine_from_config():
    config = ConfigParser()
    config.read('config.ini')
    return create_engine(config['memebrane']['dburl'])


def get_session(engine):
    Session = sessionmaker(bind=engine)
    return Session()


def populate_brains(session, brains):
    for slug, brain_def in brains.items():
        brain = Brain(id=brain_def['brain'], name=brain_def['name'],
                      base_id=brain_def['thought'], slug=slug)
        session.add(brain)


def add_to_cache(session, data, force=False):
    root_id = data['root']['id']
    nodes = {t['id']: t for t in data["thoughts"]}
    nodes.update({t['id']: t for t in data["tags"]})
    node_ids = set(nodes.keys())
    nodes_in_cache = session.query(Node).filter(Node.id.in_(nodes.keys()))
    for node in nodes_in_cache:
        node_data = nodes.pop(node.id)
        focus = node.id == root_id
        if focus:
            for t in ['tags', 'notesHtml', 'notesMarkdown']:
                if t in data:
                    node_data[t] = data[t]
        node.update_from_json(node_data, focus, force)
    for node_data in nodes.values():
        focus = node_data['id'] == root_id
        if focus:
            for t in ['tags', 'notesHtml', 'notesMarkdown']:
                if t in data:
                    node_data[t] = data[t]
        session.add(Node.create_from_json(
            node_data, node_data['id'] == root_id))
    session.flush()
    links = {l['id']: l for l in data.get("links", ())}
    links_in_cache = session.query(Link).filter(Link.id.in_(links.keys()))
    for link in links_in_cache:
        link.update_from_json(links.pop(link.id), force)
    for ldata in links.values():
        if ldata['thoughtIdA'] in node_ids and ldata['thoughtIdB'] in node_ids:
            session.add(Link.create_from_json(ldata))
        else:
            print(f"Missing node for this link:{ldata}")
    attachments = {l['id']: l for l in data.get("attachments", ())}
    attachments_in_cache = session.query(Attachment).filter(
        Attachment.id.in_(attachments.keys()))
    for attachment in attachments_in_cache:
        attachment.update_from_json(
            attachments.pop(attachment.id), force=force)
    for adata in attachments.values():
        session.add(Attachment.create_from_json(adata))
    session.commit()


def get_node(session, brain, id, cache_staleness=timedelta(days=1), force=False):
    node = session.query(Node).filter_by(id=id, brain_id=brain.id).first()
    data = None
    if force or not node or cache_staleness is None or not node.read_as_focus or datetime.now() - node.last_read > cache_staleness:
        data = get_thought_data(brain.id, id)
        if data:
            add_to_cache(session, data, force)
            if not node:
                node = session.query(Node).filter_by(
                    id=id, brain_id=brain.id).first()
    return node, data


def create_tables(engine):
    with engine.connect() as conn:
        Node.metadata.create_all(conn)


def lcase1(str):
    return str[0].lower() + str[1:]


def lcase_json(json):
    return {
        lcase1(key): lcase_json(val) if isinstance(val, dict) else val
        for key, val in json.items()
    }


if __name__ == '__main__':
    engine = get_engine()
    create_tables(engine)
    populate_brains(get_session(engine), get_config_brains())
