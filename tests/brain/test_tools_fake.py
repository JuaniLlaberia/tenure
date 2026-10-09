from brain.fakes import FakeTools
from contract import PageContent, SearchResult

RESULTS = [
    SearchResult(title=f"Result {n}", url=f"https://example.com/{n}", snippet=f"Snippet {n}")
    for n in range(3)
]
PAGE = PageContent(url="https://example.com", title="Example", text="Hello from the page")

async def test_actions_are_recorded():
    tools = FakeTools()
    await tools.post_social("b1", "Hello")
    await tools.send_email("b1", "a@b.co", "Hi", "Body")
    assert tools.calls == [
        ("post_social", {"business_id": "b1", "text": "Hello"}),
        ("send_email", {"business_id": "b1", "to": "a@b.co", "subject": "Hi", "body": "Body"}),
    ]

async def test_post_social_returns_url_and_external_id():
    tools = FakeTools()
    first = await tools.post_social("b1", "One")
    second = await tools.post_social("b1", "Two")

    assert first.ok and first.url and first.external_id
    assert first.action_id != second.action_id
    assert first.external_id != second.external_id

    deleted = await tools.delete_social("b1", first.external_id)
    assert deleted.ok
    assert tools.calls[-1] == (
        "delete_social",
        {"business_id": "b1", "external_id": first.external_id},
    )

async def test_fail_makes_methods_fail_softly():
    tools = FakeTools(search_results=RESULTS, pages={PAGE.url: PAGE})
    tools.fail = {"post_social", "delete_social", "send_email", "web_search", "fetch_page"}

    for result in [
        await tools.post_social("b1", "Hello"),
        await tools.delete_social("b1", "at://fake/post/1"),
        await tools.send_email("b1", "a@b.co", "Hi", "Body"),
    ]:
        assert not result.ok
        assert result.error
        assert result.action_id

    assert await tools.web_search("anything") == []
    assert await tools.fetch_page(PAGE.url) is None

async def test_search_and_fetch_return_configured_data():
    tools = FakeTools(search_results=RESULTS, pages={PAGE.url: PAGE})

    assert await tools.web_search("launch ideas", k=2) == RESULTS[:2]
    assert await tools.fetch_page(PAGE.url) == PAGE
    assert await tools.fetch_page("https://unknown.example") is None
    assert ("web_search", {"query": "launch ideas", "k": 2}) in tools.calls
    assert ("fetch_page", {"url": PAGE.url}) in tools.calls
