import pathlib
try:
  import simplejson as json
except ImportError:
  import json

def del_dir(dir):
  for p in dir.iterdir():
    if p.is_dir():
      del_dir(p)
    else:
      p.unlink()
  dir.rmdir()

def remove_private(path):
  nodes = set()
  path = pathlib.Path(path)
  p1 = path.joinpath("thoughts.json")
  p2 = path.joinpath("_thoughts.json")
  with p1.open() as f, p2.open('w') as f2:
      for line in f:
        node = json.loads(line)
        id = node['Id']
        if node.get("ACType", 0) == 1:
          sub = path.joinpath(id)
          if sub.exists():
            print(sub)
            del_dir(sub)
          continue
        nodes.add(id)
      f2.write(line)
  p1.unlink()
  p2.rename(p1)
  p1 = path.joinpath("attachments.json")
  p2 = path.joinpath("_attachments.json")
  with p1.open() as f, p2.open('w') as f2:
      for line in f:
        attachment = json.loads(line)
        if attachment["SourceId"] in nodes:
          f2.write(line)
  p1.unlink()
  p2.rename(p1)
  p1 = path.joinpath("modificationlogs.json")
  p2 = path.joinpath("_modificationlogs.json")
  with p1.open() as f, p2.open('w') as f2:
      for line in f:
        attachment = json.loads(line)
        if attachment["SourceId"] in nodes:
          f2.write(line)
  p1.unlink()
  p2.rename(p1)
  p1 = path.joinpath("links.json")
  p2 = path.joinpath("_links.json")
  with p1.open() as f, p2.open('w') as f2:
      for line in f:
        link = json.loads(line)
        if link["ThoughtIdA"] in nodes and link["ThoughtIdB"] in nodes:
          f2.write(line)
  p1.unlink()
  p2.rename(p1)


if __name__ == "__main__":
  from sys import argv
  remove_private(argv[1])

