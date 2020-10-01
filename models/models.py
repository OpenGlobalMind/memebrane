from datetime import datetime

from isodate import parse_datetime
from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    String,
    Unicode,
    DateTime,
    literal,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY


class BaseOps(object):
    pass

Base = declarative_base(cls=BaseOps)


class Brain(Base):
    __tablename__ = "brain"
    id = Column(UUID, primary_key=True)
    name = Column(Unicode)
    slug = Column(String, unique=True)
    base_id = Column(UUID, nullable=True)


class Node(Base):
    __tablename__ = "node"
    id = Column(UUID, primary_key=True)
    brain_id = Column(UUID, ForeignKey(Brain.id, ondelete="CASCADE"))
    data = Column(JSONB)
    tags = Column(ARRAY(Unicode))
    extra_args = {}
    tags = []
    name = Column(Unicode, nullable=False, index=True)
    last_read = Column(DateTime)
    last_modified = Column(DateTime)
    read_as_focus = Column(Boolean, default=False)
    brain = relationship(Brain, foreign_keys=[brain_id])
    # siblings = relationship("Node", secondary="Link")

    def get_neighbour_data(
            self, session, parents=True, children=True, siblings=True,
            jumps=True, full=False, with_links=False):
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
        if not queries:
            return []
        query = queries.pop()
        if queries:
            query = query.union_all(*queries)
        return query.all()

    @classmethod
    def create_from_json(cls, data, focus=False, **extra):
        data_time = parse_datetime(max(filter(None, [
            data['modificationDateTime'], data['linksModificationDateTime']])))
        return cls(
            id=data['id'],
            brain_id=data['brainId'],
            data=data,
            name=data['name'],
            last_read=datetime.now(),
            last_modified=data_time,
            read_as_focus=focus,
            **extra
        )

    def update_from_json(self, data, focus=False, force=False, **extra):
        assert data['id'] == self.id
        data_time = parse_datetime(max(filter(None, [
            data['modificationDateTime'], data['linksModificationDateTime']])))
        if focus and not self.read_as_focus:
            force = True
            self.read_as_focus = True
        self.last_read = datetime.now()
        if data_time <= self.last_modified and not force:
            return
        self.brain_id = data['brainId']
        self.data.update(data)
        self.name = data['name']
        self.last_modified = data_time
        if 'tags' in extra:
            self.tags = extra.pop('tags')
        self.data.update(extra)


class Link(Base):
    __tablename__ = "link"
    id = Column(UUID, primary_key=True)
    brain_id = Column(UUID, ForeignKey(Brain.id, ondelete="CASCADE"))
    data = Column(JSONB)
    last_modified = Column(DateTime)
    is_jump = Column(Boolean, default=False)
    parent_id = Column(UUID, ForeignKey(Node.id, ondelete="CASCADE"), index=True)
    child_id = Column(UUID, ForeignKey(Node.id, ondelete="CASCADE"), index=True)
    parent = relationship(Node, foreign_keys=[parent_id], backref="child_links")
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
