from .db import Base, engine


def init_db_and_seed():
    Base.metadata.create_all(bind=engine)
