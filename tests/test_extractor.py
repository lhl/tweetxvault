from __future__ import annotations

from tests.conftest import (
    make_article_result,
    make_photo_media,
    make_tweet_result,
    make_url_entity,
    make_video_media,
)
from tweetxvault.extractor import extract_secondary_objects, extract_thread_objects


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
