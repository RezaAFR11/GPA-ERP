from sqlalchemy import Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.query_sorting import apply_sorting


class Base(DeclarativeBase):
    pass


class SortableRecord(Base):
    __tablename__ = "test_sortable_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all([
        SortableRecord(id=1, name="Beta 10", score=10),
        SortableRecord(id=2, name="Alpha", score=None),
        SortableRecord(id=3, name="Beta 2", score=20),
    ])
    session.commit()
    return session


def test_apply_sorting_uses_requested_allowed_column_and_direction():
    with _session() as session:
        query = apply_sorting(
            session.query(SortableRecord),
            sort_by="score",
            sort_dir="desc",
            columns={"name": SortableRecord.name, "score": SortableRecord.score},
            default_key="name",
            tie_breaker=SortableRecord.id,
        )

        assert [record.id for record in query.all()] == [3, 1, 2]


def test_apply_sorting_falls_back_for_unknown_column_and_direction():
    with _session() as session:
        query = apply_sorting(
            session.query(SortableRecord),
            sort_by="score; DROP TABLE users",
            sort_dir="sideways",
            columns={"name": SortableRecord.name, "score": SortableRecord.score},
            default_key="name",
            default_dir="asc",
            tie_breaker=SortableRecord.id,
        )

        assert [record.id for record in query.all()] == [2, 1, 3]
