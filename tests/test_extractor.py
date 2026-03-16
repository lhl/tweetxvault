from __future__ import annotations

from tests.conftest import (
    make_article_result,
    make_photo_media,
    make_tweet_result,
    make_url_entity,
    make_video_media,
)
from tweetxvault.extractor import (
    ArticleData,
    ExtractedTweetGraph,
    MediaData,
    TweetObjectData,
    UrlData,
    UrlRefData,
    extract_secondary_objects,
    extract_thread_objects,
)


def test_extract_secondary_objects_captures_quote_media_urls_and_article() -> None:
    quoted = make_tweet_result(
        "200",
        "quoted short text",
        user_id="2000",
        note_text="quoted longform text",
        urls=[
            make_url_entity(
                "https://t.co/quoted",
                "https://quoted.example.com/post",
                display_url="quoted.example.com/post",
            )
        ],
        media=[
            make_video_media(
                "7_quoted",
                "https://pbs.twimg.com/ext_tw_video_thumb/quoted.jpg",
                bitrate_url="https://video.twimg.com/ext_tw_video/quoted.mp4",
            )
        ],
    )
    root = make_tweet_result(
        "100",
        "root short text",
        user_id="1000",
        urls=[
            make_url_entity(
                "https://t.co/root",
                "https://Example.com/story?utm_source=x&keep=1",
                display_url="example.com/story",
            )
        ],
        media=[make_photo_media("3_root", "https://pbs.twimg.com/media/root.jpg")],
        quoted_tweet=quoted,
        article=make_article_result(
            "article-1",
            title="Article title",
            preview_text="Article preview",
            plain_text="Article body",
            url="https://x.com/i/article/123",
        ),
    )

    graph = extract_secondary_objects(root)
    relation = next(iter(graph.relations.values()))

    assert set(graph.tweet_objects) == {"100", "200"}
    assert graph.tweet_objects["200"].text == "quoted longform text"
    assert relation.relation_type == "quote_of"
    assert relation.target_tweet_id == "200"
    assert len(graph.media) == 3
    video = graph.media[("200", "7_quoted")]
    assert video.media_url == "https://video.twimg.com/ext_tw_video/quoted-hd.mp4"
    article_cover = graph.media[("100", "article-cover:100")]
    assert article_cover.source == "article_cover"
    assert article_cover.article_id == "article-1"
    assert article_cover.media_url == "https://pbs.twimg.com/article-cover.jpg"
    assert len(graph.url_refs) == 2
    canonical_urls = {item.canonical_url for item in graph.urls.values()}
    assert "https://example.com/story?keep=1" in canonical_urls
    assert "https://quoted.example.com/post" in canonical_urls
    article = graph.articles["100"]
    assert article.title == "Article title"
    assert article.content_text == "Article body"
    assert article.status == "body_present"
    assert article.canonical_url == "https://x.com/i/article/123"


def test_extract_secondary_objects_captures_retweet_relation() -> None:
    original = make_tweet_result("300", "original tweet", user_id="3000")
    wrapper = make_tweet_result(
        "301",
        "RT @user3000: original tweet",
        user_id="3010",
        retweeted_tweet=original,
    )

    graph = extract_secondary_objects(wrapper)
    relation = next(iter(graph.relations.values()))

    assert set(graph.tweet_objects) == {"300", "301"}
    assert relation.relation_type == "retweet_of"
    assert relation.target_tweet_id == "300"


def test_extract_thread_objects_adds_reply_and_link_relations() -> None:
    parent = make_tweet_result("200", "parent tweet", user_id="2000")
    reply = make_tweet_result(
        "100",
        "reply tweet",
        user_id="1000",
        in_reply_to_status_id="200",
        urls=[
            make_url_entity(
                "https://t.co/link",
                "https://x.com/example/status/300?s=20",
                display_url="x.com/example/status/300",
            )
        ],
        conversation_id="200",
    )

    graph = extract_thread_objects([reply, parent])
    relations = {
        (item.source_tweet_id, item.relation_type, item.target_tweet_id)
        for item in graph.relations.values()
    }

    assert ("100", "reply_to", "200") in relations
    assert ("100", "thread_parent", "200") in relations
    assert ("200", "thread_child", "100") in relations
    assert ("100", "links_to_status", "300") in relations


def test_extract_secondary_objects_reuses_one_url_candidate_helper() -> None:
    root = make_tweet_result(
        "100",
        "root short text",
        user_id="1000",
        urls=[
            make_url_entity(
                "https://t.co/final",
                "https://expanded.example.com/story?utm_source=x",
                display_url="expanded.example.com/story",
                unwound_url={
                    "url": {"string_value": "https://final.example.com/story?utm_source=x&keep=1"},
                    "title": "Final title",
                    "description": "Final description",
                    "site_name": "Final Site",
                },
            ),
            {
                "url": "https://t.co/shortonly",
                "display_url": "t.co/shortonly",
            },
        ],
    )

    graph = extract_secondary_objects(root)
    urls_by_canonical = {item.canonical_url: item for item in graph.urls.values()}

    final = urls_by_canonical["https://final.example.com/story?keep=1"]
    assert final.final_url == "https://final.example.com/story?utm_source=x&keep=1"
    assert final.title == "Final title"
    assert final.description == "Final description"
    assert final.site_name == "Final Site"

    short_only = next(
        item for item in graph.url_refs.values() if item.short_url == "https://t.co/shortonly"
    )
    assert short_only.canonical_url == "https://t.co/shortonly"
    short_only_resolved = urls_by_canonical["https://t.co/shortonly"]
    assert short_only_resolved.final_url is None


def test_extract_secondary_objects_ignores_invalid_article_and_attached_payloads() -> None:
    root = make_tweet_result("100", "root", user_id="1000")
    root["article"] = {"article_results": {"result": "bad"}}
    root["quoted_status_result"] = {"result": {"__typename": "TweetUnavailable"}}
    root["legacy"]["retweeted_status_result"] = {"result": {"not": "a tweet"}}

    graph = extract_secondary_objects(root)

    assert set(graph.tweet_objects) == {"100"}
    assert not graph.relations
    assert not graph.articles


def test_extract_secondary_objects_handles_sparse_url_and_media_items() -> None:
    root = make_tweet_result("100", "root", user_id="1000")
    root["legacy"]["entities"] = {
        "urls": [
            {"url": "https://t.co/shortonly"},
            {"expanded_url": "relative/path"},
            "bad",
        ]
    }
    root["legacy"]["extended_entities"] = {
        "media": [
            {"type": "photo"},
            {
                "media_key": "7",
                "type": "video",
                "video_info": {"variants": ["bad", {"url": ""}]},
            },
            "bad",
        ]
    }

    graph = extract_secondary_objects(root)

    assert len(graph.urls) == 1
    assert next(iter(graph.urls.values())).canonical_url == "https://t.co/shortonly"
    assert set(graph.media) == {("100", "idx-0"), ("100", "7")}
    assert graph.media[("100", "idx-0")].media_url is None
    assert graph.media[("100", "7")].media_url is None


def test_extracted_tweet_graph_add_tweet_object_prefers_new_non_empty_fields() -> None:
    graph = ExtractedTweetGraph()
    graph.add_tweet_object(
        TweetObjectData(
            tweet_id="100",
            text="old text",
            author_id="old-author",
            author_username="old-user",
            author_display_name="Old Name",
            created_at="2026-03-14T00:00:00+00:00",
            conversation_id="conv-old",
            lang="en",
            note_tweet_text="old note",
            raw_json={"old": True},
        )
    )
    graph.add_tweet_object(
        TweetObjectData(
            tweet_id="100",
            text="",
            author_id="new-author",
            author_username=None,
            author_display_name="New Name",
            created_at=None,
            conversation_id="conv-new",
            lang="",
            note_tweet_text="",
            raw_json={},
        )
    )

    item = graph.tweet_objects["100"]
    assert item.text == "old text"
    assert item.author_id == "new-author"
    assert item.author_username == "old-user"
    assert item.author_display_name == "New Name"
    assert item.created_at == "2026-03-14T00:00:00+00:00"
    assert item.conversation_id == "conv-new"
    assert item.lang == "en"
    assert item.note_tweet_text == "old note"
    assert item.raw_json == {"old": True}


def test_extracted_tweet_graph_add_media_applies_special_merge_rules() -> None:
    graph = ExtractedTweetGraph()
    graph.add_media(
        MediaData(
            tweet_id="100",
            position=3,
            media_key="m1",
            media_type="photo",
            media_url="https://cdn.example.com/old.jpg",
            thumbnail_url="https://cdn.example.com/old-thumb.jpg",
            width=100,
            height=50,
            duration_millis=None,
            variants=[{"url": "https://cdn.example.com/old.jpg"}],
            raw_json={"old": True},
            source="tweet_media",
            article_id="article-old",
        )
    )
    graph.add_media(
        MediaData(
            tweet_id="100",
            position=1,
            media_key="m1",
            media_type=None,
            media_url="https://cdn.example.com/new.jpg",
            thumbnail_url="",
            width=None,
            height=60,
            duration_millis=123,
            variants=[],
            raw_json={},
            source="",
            article_id="article-new",
        )
    )

    item = graph.media[("100", "m1")]
    assert item.position == 1
    assert item.media_type == "photo"
    assert item.media_url == "https://cdn.example.com/new.jpg"
    assert item.thumbnail_url == "https://cdn.example.com/old-thumb.jpg"
    assert item.width == 100
    assert item.height == 60
    assert item.duration_millis == 123
    assert item.variants == [{"url": "https://cdn.example.com/old.jpg"}]
    assert item.raw_json == {"old": True}
    assert item.source == "tweet_media"
    assert item.article_id == "article-new"


def test_extracted_tweet_graph_merge_coalesces_url_url_ref_and_article_records() -> None:
    graph = ExtractedTweetGraph()
    graph.add_url(
        UrlData(
            url_hash="url-hash",
            canonical_url="https://example.com/story",
            expanded_url="https://example.com/story?ref=old",
            host="example.com",
            raw_json={"old": True},
            final_url=None,
            title="Old title",
            description="Old description",
            site_name="Old Site",
        )
    )
    graph.add_url_ref(
        UrlRefData(
            tweet_id="100",
            position=0,
            short_url="https://t.co/short",
            expanded_url=None,
            canonical_url=None,
            display_url="t.co/short",
            url_hash=None,
            raw_json={"old": True},
        )
    )
    graph.add_article(
        ArticleData(
            tweet_id="100",
            article_id="article-1",
            title="Old article title",
            summary_text="Old summary",
            content_text=None,
            canonical_url="https://x.com/i/article/1",
            published_at="2026-03-14T00:00:00+00:00",
            status="preview_only",
            raw_json={"old": True},
        )
    )

    other = ExtractedTweetGraph()
    other.add_url(
        UrlData(
            url_hash="url-hash",
            canonical_url="https://example.com/story",
            expanded_url="",
            host=None,
            raw_json={},
            final_url="https://example.com/final",
            title="",
            description="New description",
            site_name=None,
        )
    )
    other.add_url_ref(
        UrlRefData(
            tweet_id="100",
            position=0,
            short_url=None,
            expanded_url="https://example.com/story",
            canonical_url="https://example.com/story",
            display_url="",
            url_hash="url-hash",
            raw_json={},
        )
    )
    other.add_article(
        ArticleData(
            tweet_id="100",
            article_id="",
            title="New article title",
            summary_text="",
            content_text="Article body",
            canonical_url=None,
            published_at=None,
            status="preview_only",
            raw_json={"new": True},
        )
    )

    graph.merge(other)

    url = graph.urls["url-hash"]
    assert url.expanded_url == "https://example.com/story?ref=old"
    assert url.final_url == "https://example.com/final"
    assert url.title == "Old title"
    assert url.description == "New description"
    assert url.site_name == "Old Site"
    assert url.raw_json == {"old": True}

    url_ref = graph.url_refs[("100", 0)]
    assert url_ref.short_url == "https://t.co/short"
    assert url_ref.expanded_url == "https://example.com/story"
    assert url_ref.canonical_url == "https://example.com/story"
    assert url_ref.display_url == "t.co/short"
    assert url_ref.url_hash == "url-hash"
    assert url_ref.raw_json == {"old": True}

    article = graph.articles["100"]
    assert article.article_id == "article-1"
    assert article.title == "New article title"
    assert article.summary_text == "Old summary"
    assert article.content_text == "Article body"
    assert article.canonical_url == "https://x.com/i/article/1"
    assert article.published_at == "2026-03-14T00:00:00+00:00"
    assert article.status == "body_present"
    assert article.raw_json == {"new": True}
