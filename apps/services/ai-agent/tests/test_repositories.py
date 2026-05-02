import pytest
from semantic_kernel.contents import ChatHistory

from app.repositories import InMemoryApprovalRepository, InMemorySessionRepository
from app.schemas import ApprovalRecord, Plan


@pytest.fixture
def session_repo() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture
def approval_repo() -> InMemoryApprovalRepository:
    return InMemoryApprovalRepository()


class TestInMemorySessionRepository:
    async def test_get_missing_returns_none(self, session_repo):
        result = await session_repo.get("nonexistent")
        assert result is None

    async def test_save_and_get_roundtrip(self, session_repo):
        history = ChatHistory()
        history.add_user_message("hello")
        await session_repo.save("s1", history)

        fetched = await session_repo.get("s1")
        assert fetched is history

    async def test_save_overwrites(self, session_repo):
        h1 = ChatHistory()
        h2 = ChatHistory()
        await session_repo.save("s1", h1)
        await session_repo.save("s1", h2)

        assert await session_repo.get("s1") is h2

    async def test_delete_removes_entry(self, session_repo):
        history = ChatHistory()
        await session_repo.save("s1", history)
        await session_repo.delete("s1")

        assert await session_repo.get("s1") is None

    async def test_delete_nonexistent_is_noop(self, session_repo):
        await session_repo.delete("nonexistent")  # should not raise


class TestInMemoryApprovalRepository:
    async def test_get_missing_returns_none(self, approval_repo):
        result = await approval_repo.get("nonexistent")
        assert result is None

    async def test_save_and_get_roundtrip(self, approval_repo):
        record = ApprovalRecord(
            session_id="s1",
            plan=Plan(tool_name="send_reply", is_side_effecting=True),
        )
        await approval_repo.save(record)

        fetched = await approval_repo.get(record.id)
        assert fetched is record

    async def test_save_overwrites_on_same_id(self, approval_repo):
        record = ApprovalRecord(
            session_id="s1",
            plan=Plan(tool_name="send_reply", is_side_effecting=True),
        )
        await approval_repo.save(record)
        record.status = "approved"
        await approval_repo.save(record)

        fetched = await approval_repo.get(record.id)
        assert fetched.status == "approved"

    async def test_ids_are_unique(self, approval_repo):
        r1 = ApprovalRecord(session_id="s1", plan=Plan())
        r2 = ApprovalRecord(session_id="s2", plan=Plan())
        assert r1.id != r2.id
