from pathlib import Path
from sys import argv

import simplejson as json

from .models import Node, Link, Attachment, AttachmentType
from .utils import get_brain, get_session, lcase_json, engine_from_config, get_session


def read_brain(base: Path, session):
    with open(base.joinpath("meta.json")) as f:
        meta = json.load(f)
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
            if link['ThoughtIdA'] == '00000000-0000-0000-0000-000000000000' or \
                    link['ThoughtIdB'] == '00000000-0000-0000-0000-000000000000':
                continue
            link = Link.create_or_update_from_json(session, lcase_json(link))
            session.add(link)
    with open(base.joinpath("attachments.json")) as f:
        for line in f:
            att = json.loads(line)
            content = None
            if att['Type'] not in (
                    AttachmentType.ExternalFile.value,
                    AttachmentType.ExternalUrl.value,
                    AttachmentType.ExternalDirectory.value):
                contentf = base.joinpath(att["SourceId"], att["Location"])
                if contentf.exists():
                    with contentf.open(mode='rb') as f2:
                        content = f2.read()
                else:
                    contentf = base.joinpath(att["SourceId"], "Notes", att["Location"])
                    if contentf.exists():
                        with contentf.open(mode='rb') as f2:
                            content = f2.read()
            att = Attachment.create_or_update_from_json(
                session, lcase_json(att), content)
            session.add(att)


if __name__ == '__main__':
    fname = argv[1]
    session = get_session()
    read_brain(Path(fname), session)
    session.commit()
