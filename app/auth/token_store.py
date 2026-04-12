_store: dict[int, str] = {}


def put_token(github_id: int, access_token: str) -> None:
    _store[int(github_id)] = access_token


def get_token(github_id: int) -> str | None:
    return _store.get(int(github_id))


def clear_token(github_id: int) -> None:
    _store.pop(int(github_id), None)
