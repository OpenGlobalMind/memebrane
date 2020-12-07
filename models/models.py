from datetime import datetime

from isodate import parse_datetime
from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    String,
    Unicode,
    DateTime,
    Index,
    Text,
    literal,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql.sqltypes import BLOB


if True:
    from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
else:
    UUID = String
    JSONB = Text


class BaseOps(object):
    pass


Base = declarative_base(cls=BaseOps)


class Brain(Base):
    __tablename__ = "brain"
    id = Column(UUID, primary_key=True)
    name = Column(Unicode)
    slug = Column(String, unique=True)
    base_id = Column(UUID, nullable=True)

    @property
    def safe_slug(self):
        return self.slug or self.id


class Node(Base):
    __tablename__ = "node"
    __table_args__ = (
        Index("node_name_vidx",
              func.to_tsvector('simple', 'node.name'),
              postgresql_using='gin'),
        Index("node_tags_idx", 'tags', postgresql_using='gin'),
    )
    id = Column(UUID, primary_key=True)
    brain_id = Column(UUID, ForeignKey(
        Brain.id, ondelete="CASCADE"), nullable=False)
    data = Column(JSONB)
    tags = Column(ARRAY(UUID))
    name = Column(Unicode, nullable=False)
    last_read = Column(DateTime)
    last_modified = Column(DateTime)
    read_as_focus = Column(Boolean, default=False)
    is_tag = Column(Boolean, default=False)
    brain = relationship(Brain, foreign_keys=[brain_id])
    # siblings = relationship("Node", secondary="Link")

    def get_neighbour_data(
            self, session, parents=True, children=True, siblings=True,
            jumps=True, tags=True, of_tags=True, full=False, with_links=False):
        queries = []
        entities = [Node] if full else [Node.id, Node.name]
        if with_links:
            entities.append(Link)
        if parents or siblings:
            parent_query = session.query(
                literal('parent'), *entities).join(
                Link, Node.child_links).filter(
                (Link.child_id == self.id) & ~Link.is_jump)
        if parents:
            queries.append(parent_query)
        if children:
            queries.append(session.query(
                literal('child'), *entities).join(
                Link, Node.parent_links).filter(
                (Link.parent_id == self.id) & ~Link.is_jump))
        if siblings:
            subquery = parent_query.with_entities(Node.id).subquery()
            queries.append(session.query(
                literal('sibling'), *entities).join(
                Link, Node.parent_links).filter(
                Link.parent_id.in_(subquery) &
                (Node.id != self.id) & ~Link.is_jump))
        if jumps:
            queries.append(session.query(
                literal('jump'), *entities).join(
                Link, Node.parent_links).filter(
                (Link.parent_id == self.id) & Link.is_jump))
            queries.append(session.query(
                literal('jump'), *entities).join(
                Link, Node.child_links).filter(
                (Link.child_id == self.id) & Link.is_jump))
        if tags and self.tags:
            query = session.query(
                literal('tag'), *entities).filter(Node.id.in_(self.tags))
            if with_links:
                query = query.outerjoin(Link, Link.id == None)
            queries.append(query)
        if of_tags:
            query = session.query(
                literal('of_tag'), *entities).filter(Node.tags.contains([self.id]))
            if with_links:
                query = query.outerjoin(Link, Link.id == None)
            queries.append(query)
        if not queries:
            return []
        query = queries.pop()
        if queries:
            query = query.union_all(*queries)
        return query.all()

    @classmethod
    def create_from_json(cls, data, focus=False):
        data_time = parse_datetime(max(filter(None, [
            data['modificationDateTime'], data.get('linksModificationDateTime', None)])))
        tags = data.pop('tags', None)
        if tags:
            tags = [t['id'] for t in tags]
        return cls(
            id=data['id'],
            brain_id=data['brainId'],
            data=data,
            name=data['name'],
            last_read=datetime.now(),
            last_modified=data_time,
            read_as_focus=focus,
            is_tag=data.get('kind', 1) == 4,
            tags=tags
        )

    @ classmethod
    def create_or_update_from_json(cls, session, data, force=False):
        i = session.query(cls).filter_by(id=data["id"]).one()
        if i:
            i.update_from_json(data, force)
        else:
            i = cls.create_from_json(data)
        return i

    def update_from_json(self, data, focus=False, force=False):
        assert data['id'] == self.id
        data_time = parse_datetime(max(filter(None, [
            data['modificationDateTime'], data.get('linksModificationDateTime', None)])))
        if focus and not self.read_as_focus:
            force = True
            self.read_as_focus = True
        self.last_read = datetime.now()
        if data_time <= self.last_modified and not force:
            return
        self.brain_id = data['brainId']
        tags = data.pop('tags', None)
        if tags:
            tags = [t['id'] for t in tags]
            self.tags = tags
        self.data.update(data)
        flag_modified(self, 'data')
        self.name = data['name']
        self.last_modified = data_time
        self.is_tag = data.get('kind', 1) == 4


class Link(Base):
    __tablename__ = "link"
    id = Column(UUID, primary_key=True)
    brain_id = Column(UUID, ForeignKey(
        Brain.id, ondelete="CASCADE"), nullable=False)
    data = Column(JSONB)
    last_modified = Column(DateTime)
    is_jump = Column(Boolean, default=False)
    parent_id = Column(UUID, ForeignKey(
        Node.id, ondelete="CASCADE"), nullable=False, index=True)
    child_id = Column(UUID, ForeignKey(
        Node.id, ondelete="CASCADE"), nullable=False, index=True)
    parent = relationship(Node, foreign_keys=[
                          parent_id], backref="child_links")
    child = relationship(Node, foreign_keys=[child_id], backref="parent_links")

    @classmethod
    def create_from_json(cls, data):
        return cls(
            id=data['id'],
            brain_id=data['brainId'],
            data=data,
            is_jump=data['relation'] != 1,
            last_modified=parse_datetime(data['modificationDateTime']),
            parent_id=data['thoughtIdA'],
            child_id=data['thoughtIdB']
        )

    @ classmethod
    def create_or_update_from_json(cls, session, data, force=False):
        i = session.query(cls).filter_by(id=data["id"]).one()
        if i:
            i.update_from_json(data, force)
        else:
            i = cls.create_from_json(data)
        return i

    def update_from_json(self, data, force=False):
        assert data['id'] == self.id
        data_time = parse_datetime(data['modificationDateTime'])
        if data_time <= self.last_modified and not force:
            return
        self.is_jump = data['relation'] != 1
        self.brain_id = data['brainId']
        self.data = data
        self.last_modified = data_time
        self.parent_id = data['thoughtIdA']
        self.child_id = data['thoughtIdB']


Node.children = relationship(
    Node, secondary=Link.__table__,
    primaryjoin=Node.id == Link.parent_id,
    secondaryjoin=Node.id == Link.child_id)
Node.parents = relationship(
    Node, secondary=Link.__table__,
    primaryjoin=Node.id == Link.child_id,
    secondaryjoin=Node.id == Link.parent_id)


class Attachment(Base):
    __tablename__ = "attachment"
    id = Column(UUID, primary_key=True)
    brain_id = Column(UUID, ForeignKey(
        Brain.id, ondelete="CASCADE"), nullable=False)
    data = Column(JSONB)
    last_modified = Column(DateTime)
    location = Column(Unicode, nullable=False)
    node_id = Column(UUID, ForeignKey(
        Node.id, ondelete="CASCADE"), nullable=False)
    content = Column(BLOB)
    node = relationship(Node, backref="attachments")

    @ classmethod
    def create_or_update_from_json(cls, session, data, content=None, force=False):
        i = session.query(cls).filter_by(id=data["id"]).one()
        if i:
            i.update_from_json(data, content, force)
        else:
            i = cls.create_from_json(data, content)
        return i

    @ classmethod
    def create_from_json(cls, data, content=None):
        return cls(
            id=data['id'],
            brain_id=data['brainId'],
            data=data,
            content=content,
            last_modified=parse_datetime(data['modificationDateTime']),
            location=data['location'],
            node_id=data['sourceId']
        )

    def update_from_json(self, data, content=None, force=False):
        assert data['id'] == self.id
        data_time = parse_datetime(data['modificationDateTime'])
        if data_time <= self.last_modified and not force:
            return
        self.brain_id = data['brainId']
        self.data = data
        self.last_modified = data_time
        self.location = data['location']
        self.node_id = data['sourceId']
        if content:
            self.content = content

    @ property
    def name(self):
        return self.data['name']
