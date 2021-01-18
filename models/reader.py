import simplejson as json

from .models import Node, Link, Attachment, AttachmentType
from .utils import get_brain, get_session, lcase_json, get_session


def read_brain(base, session):
    node_ids = set()
    with base.joinpath("meta.json").open() as f:
        meta = json.load(f)
        brain_id = meta["BrainId"]
        brain = get_brain(session, brain_id)
    with base.joinpath("thoughts.json").open() as f:
        for line in f:
            node = json.loads(line)
            node_ids.add(node["Id"])
            node = Node.create_or_update_from_json(session, lcase_json(node))
            session.add(node)
    with base.joinpath("links.json").open() as f:
        for line in f:
            link = json.loads(line)
            if link['ThoughtIdA'] not in node_ids or \
                    link['ThoughtIdB'] not in node_ids:
                print("Missing link: ", link["Id"], link["ThoughtIdA"], link["ThoughtIdB"])
                continue
            link = Link.create_or_update_from_json(session, lcase_json(link))
            session.add(link)
    with base.joinpath("attachments.json").open() as f:
        for line in f:
            att = json.loads(line)
            if att["SourceId"] not in node_ids:
                print("Missing attachment: ", att["Id"], att["SourceId"])
                continue
            content = None
            if att['Type'] not in (
                    AttachmentType.ExternalFile.value,
                    AttachmentType.ExternalUrl.value,
                    AttachmentType.ExternalDirectory.value):
                contentf = base.joinpath(att["SourceId"]).joinpath(att["Location"])
                if contentf.exists():
                    with contentf.open(mode='rb') as f2:
                        content = f2.read()
                else:
                    contentf = base.joinpath(att["SourceId"]).joinpath("Notes").joinpath(att["Location"])
                    if contentf.exists():
                        with contentf.open(mode='rb') as f2:
                            content = f2.read()
            att = Attachment.create_or_update_from_json(
                session, lcase_json(att), content)
            session.add(att)


if __name__ == '__main__':
    from sys import argv
    import zipfile
    fname = argv[1]
    if zipfile.is_zipfile(fname):
        root = zipfile.Path(zipfile.ZipFile(open(fname, 'rb')))
    else:
        from pathlib import Path
        root = Path(fname)
        assert root.is_dir()
    session = get_session()
    read_brain(root, session)
    session.commit()
