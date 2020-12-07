from pathlib import Path
from sys import argv

import simplejson as json

from .models import Node, Link, Attachment
from .utils import get_brain, get_session, lcase_json, get_engine, get_session


def read_brain(base: Path, session):
    with open(base.joinpath("meta.json")) as f:
        meta = json.load(f)
        brain_id = meta["BrainId"]
        brain = get_brain(session, brain_id)
    with open(base.joinpath("thoughts.json")) as f:
        for line in f:
            node = json.loads(line)
            node_base = base.joinpath(node["Id"])
            attf = node_base.joinpath("Notes.md")
            if attf.exists():
                with attf.open() as f2:
                    node["notesMarkdown"] = f2.read()
            attf = node_base.joinpath("Notes").joinpath("notes.html")
            if attf.exists():
                with attf.open() as f2:
                    node["notesHtml"] = f2.read()
            node = Node.create_from_json(lcase_json(node))
            session.add(node)
    with open(base.joinpath("links.json")) as f:
        for line in f:
            link = json.loads(line)
            link = Link.create_from_json(lcase_json(link))
            session.add(link)
    with open(base.joinpath("attachments.json")) as f:
        for line in f:
            att = json.loads(line)
            att = Attachment.create_from_json(lcase_json(att))
            session.add(att)


if __name__ == '__main__':
    fname = argv[1]
    engine = get_engine()
    session = get_session(engine)
    read_brain(Path(fname), session)
    session.commit()
