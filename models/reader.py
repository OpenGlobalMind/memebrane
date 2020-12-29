from pathlib import Path
from sys import argv

import simplejson as json

from .models import Node, Link, Attachment
from .utils import get_brain, get_session, lcase_json, engine_from_config, get_session


def read_brain(base: Path, session):
    if base.joinpath("meta.json").exists():
        with open(base.joinpath("meta.json")) as f:
            meta = json.load(f)
            brain_id = meta["BrainId"]
            brain = get_brain(session, brain_id)
    else:
        with open(base.joinpath("thoughts.json")) as f:
            line = next(f)
            meta = json.loads(line)
            brain_id = meta["BrainId"]
            brain = get_brain(session, brain_id)

    with open(base.joinpath("thoughts.json")) as f:
        for line in f:
            node = json.loads(line)
            node_base = base.joinpath(node["Id"])
            node = Node.create_or_update_from_json(session, lcase_json(node))
            session.add(node)
    with open(base.joinpath("links.json")) as f:
        for line in f:
            link = json.loads(line)
            link = Link.create_or_update_from_json(session, lcase_json(link))
            session.add(link)
    with open(base.joinpath("attachments.json")) as f:
        for line in f:
            att = json.loads(line)
            contentf = base.joinpath(att["SourceId"], att["Location"])
            if contentf.exists():
                print(contentf)
                with contentf.open(mode='rb') as f2:
                    content = f2.read()
            else:
                contentf = base.joinpath(att["SourceId"], "Notes", att["Location"])
                if contentf.exists():
                    print(contentf)
                    with contentf.open(mode='rb') as f2:
                        content = f2.read()
                else:
                    #print (contentf)less 
                    content = None
            att = Attachment.create_or_update_from_json(
                session, lcase_json(att), content)
            print(len(att.content or att.text_content or ''))
            session.add(att)


if __name__ == '__main__':
    fname = argv[1]
    session = get_session()
    read_brain(Path(fname), session)
    session.commit()
