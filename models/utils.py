from os.path import join, dirname
import json
from datetime import timedelta, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import requests

from .models import Node, Brain, Link, Attachment

CONFIG_BRAINS = None
BRAINS = {}


def get_thought_data(brain_id, thought_id):
    print(thought_id)
    r = requests.get('https://api.thebrain.com/api-v11/brains/' + brain_id + '/thoughts/' + thought_id + '/graph')
    try:
        return r.json()
    except Exception as e:
        return None


def get_config_brains():
    global CONFIG_BRAINS
    if CONFIG_BRAINS is None:
        with open(join(dirname(dirname(__file__)), 'brains.json')) as f:
            CONFIG_BRAINS = json.load(f)

def get_brain(session, slug):
    global CONFIG_BRAINS, BRAINS
    config_brains = get_config_brains()
    brain_data = CONFIG_BRAINS.get(slug, None)
    brain = BRAINS.get(slug, None)
    if not brain:
        brain = session.query(Brain).filter_by(slug=slug).first()
        if brain_data and not brain:
            brain = add_brain(session, slug, brain_data['brain'],
                              brain_data['name'], brain_data.get('thought', None))
        BRAINS[slug] = brain  # may be None, cache that
    return brain


def add_brain(session, slug, id, name, base_id=None):
    global BRAINS
    brain = Brain(id=id, name=name, base_id=base_id, slug=slug)
    session.add(brain)
    session.commit()
    BRAINS[slug] = brain
    return brain


def get_engine(password='memebrane', user='memebrane', host='localhost', db='memebrane', port=5432):
    # TODO eliminate
    return create_engine(f'postgresql://{user}:{password}@{host}:{port}/{db}')


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
    nodes_in_cache = session.query(Node).filter(Node.id.in_(nodes.keys()))
    for node in nodes_in_cache:
        node_data = nodes.pop(node.id)
        focus = node.id == root_id
        if focus:
            node_data['attachments'] = data['root']['attachments']
            for t in ['tags', 'notesHtml', 'notesMarkdown']:
                if t in data:
                    node_data[t] = data[t]
        node.update_from_json(node_data, focus, force)
    for ndata in nodes.values():
        session.add(Node.create_from_json(ndata, ndata['id'] == root_id))
    session.flush()
    links = {l['id']: l for l in data.get("links", ())}
    links_in_cache = session.query(Link).filter(Link.id.in_(links.keys()))
    for link in links_in_cache:
        link.update_from_json(links.pop(link.id), force)
    for ldata in links.values():
        session.add(Link.create_from_json(ldata))
    attachments = {l['id']: l for l in data.get("attachments", ())}
    attachments_in_cache = session.query(Attachment).filter(
        Attachment.id.in_(attachments.keys()))
    for attachment in attachments_in_cache:
        attachment.update_from_json(attachments.pop(attachment.id), force)
    for adata in attachments.values():
        session.add(Attachment.create_from_json(adata))
    session.commit()


def get_node(session, brain, id, cache_staleness=timedelta(days=1), force=False):
    node = session.query(Node).filter_by(id=id, brain_id=brain.id).first()
    data = None
    if not node or cache_staleness is None or not node.read_as_focus or datetime.now() - node.last_read > cache_staleness:
        data = get_thought_data(brain.id, id)
        if data:
            add_to_cache(session, data, force)
            if not node:
                node = session.query(Node).filter_by(id=id, brain_id=brain.id).first()
    return node, data


def create_tables(engine):
    with engine.connect() as conn:
        Node.metadata.create_all(conn)


if __name__ == '__main__':
    engine = get_engine()
    create_tables(engine)
    populate_brains(get_session(engine), get_config_brains())
