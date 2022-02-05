import asyncio

import simplejson as json

from .models import Node, Link, Attachment, AttachmentType
from .utils import get_brain, get_session, lcase_json, get_session


async def read_brain(base):
    session = get_session()
    node_ids = set()
    with (base / "meta.json").open() as f:
        meta = json.load(f)
        brain_id = meta["BrainId"]
        brain = get_brain(session, brain_id)
    with (base / "thoughts.json").open() as f:
        for line in f:
            node = json.loads(line)
            node_ids.add(node["Id"])
            node = await Node.create_or_update_from_json(session, lcase_json(node), True)
            session.add(node)
    with (base / "links.json").open() as f:
        for line in f:
            link = json.loads(line)
            if link['ThoughtIdA'] not in node_ids or \
                    link['ThoughtIdB'] not in node_ids:
                print("Missing link: ", link["Id"], link["ThoughtIdA"], link["ThoughtIdB"])
                continue
            link = await Link.create_or_update_from_json(session, lcase_json(link))
            session.add(link)
    with (base / "attachments.json").open() as f:
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
                contentf = base / att["SourceId"] / att["Location"]
                if contentf.exists():
                    with contentf.open(mode='rb') as f2:
                        content = f2.read()
                else:
                    contentf = base / att["SourceId"] / "Notes" / att["Location"]
                    if contentf.exists():
                        with contentf.open(mode='rb') as f2:
                            content = f2.read()
            att = await Attachment.create_or_update_from_json(
                session, lcase_json(att), content)
            session.add(att)
    await session.commit()

if __name__ == '__main__':
    from sys import argv
    import zipfile
    from pathlib import Path
    root = Path(argv[1])
    if zipfile.is_zipfile(root):
        root = zipfile.Path(zipfile.ZipFile(root.open('rb')))
    else:
        assert root.is_dir()
    asyncio.run(read_brain(root))
