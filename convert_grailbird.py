#!/usr/bin/env python3
"""
Convert old Twitter "Grailbird" format archives to modern format for tweetxvault.

Twitter used the "Grailbird" format for archive exports before ~2018. These older
archives have a different structure:
- tweets.csv file in the root directory
- Monthly JavaScript files in data/js/tweets/
- Different manifest structure

This script converts Grailbird archives to the modern format that tweetxvault expects,
preserving all tweet data including replies, retweets, and URLs.

Usage:
    python convert_grailbird.py <input_dir> <output_dir>

Example:
    python convert_grailbird.py TwitterArchive-2015 TwitterArchive-converted
    tweetxvault import x-archive TwitterArchive-converted
"""

import csv
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_user_details(user_details_path):
    """
    Parse user_details.js from Grailbird archive.

    Args:
        user_details_path: Path to user_details.js file

    Returns:
        Dictionary with id, screen_name, full_name, created_at or None if parsing fails
    """
    try:
        with open(user_details_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract the JavaScript object.
        # Format: var user_details = { ... };
        match = re.search(r'var user_details\s*=\s*(\{.*\})\s*;?\s*$', content, re.DOTALL)
        if not match:
            return None

        js_obj = match.group(1)
        # Normalize light JavaScript object syntax into valid JSON.
        normalized = re.sub(r',(\s*[}\]])', r'\1', js_obj)
        user_data = json.loads(normalized)

        if 'id' in user_data and 'screen_name' in user_data:
            return {
                'id': str(user_data.get('id', 'unknown')),
                'screen_name': str(user_data.get('screen_name', 'unknown')),
                'full_name': str(user_data.get('full_name', 'Unknown User')),
                'created_at': str(user_data.get('created_at', '2006-01-01 00:00:00 +0000'))
            }
    except Exception as e:
        print(f"Warning: Failed to parse user_details.js: {e}")
        return None

    return None


def parse_grailbird_timestamp(timestamp_str):
    """
    Parse Grailbird timestamp format and convert to datetime.

    Args:
        timestamp_str: Timestamp in format "2015-01-10 20:59:42 +0000"

    Returns:
        datetime object or None if parsing fails
    """
    try:
        return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def parse_timestamp(timestamp_str):
    """
    Convert Grailbird timestamp to modern Twitter archive format.

    Args:
        timestamp_str: Timestamp in format "2015-01-10 20:59:42 +0000"

    Returns:
        Timestamp in format "Sat Jan 10 20:59:42 +0000 2015"
    """
    dt = parse_grailbird_timestamp(timestamp_str)
    if dt:
        return dt.strftime("%a %b %d %H:%M:%S %z %Y")

    print(f"Warning: Failed to parse timestamp '{timestamp_str}', using as-is")
    return timestamp_str


def convert_csv_to_tweet_object(row):
    """
    Convert a Grailbird CSV row to a modern tweet object.

    Args:
        row: Dictionary containing CSV row data

    Returns:
        Dictionary in modern Twitter archive format
    """
    tweet_id = row.get("tweet_id", "")
    timestamp = row.get("timestamp", "")
    text = row.get("text", "")
    source = row.get("source", "")
    in_reply_to_status_id = row.get("in_reply_to_status_id", "")
    in_reply_to_user_id = row.get("in_reply_to_user_id", "")
    retweeted_status_id = row.get("retweeted_status_id", "")
    retweeted_status_user_id = row.get("retweeted_status_user_id", "")
    retweeted_status_timestamp = row.get("retweeted_status_timestamp", "")
    expanded_urls = row.get("expanded_urls", "")

    # Parse URLs
    # URLs are separated by commas, but URLs themselves can contain commas
    # (e.g., http://www.latimes.com/...20140113,0,5661959.story)
    # Use lookahead to split only on commas followed by http:// or https://
    urls = []
    if expanded_urls:
        # Split on comma only when followed by http:// or https://
        url_list = re.split(r',(?=https?://)', expanded_urls)

        # Deduplicate while preserving order
        seen = set()
        for url in url_list:
            url = url.strip()
            if url and url not in seen:
                seen.add(url)
                urls.append({
                    "url": url,
                    "expanded_url": url,
                    "display_url": url,
                    "indices": ["0", "0"]
                })

    # Build base tweet object
    tweet = {
        "tweet": {
            "retweeted": bool(retweeted_status_id),
            "source": source,
            "entities": {
                "hashtags": [],
                "symbols": [],
                "user_mentions": [],
                "urls": urls
            },
            "display_text_range": ["0", str(len(text))],
            "favorite_count": "0",
            "id_str": tweet_id,
            "truncated": False,
            "retweet_count": "0",
            "id": tweet_id,
            "created_at": parse_timestamp(timestamp),
            "favorited": False,
            "full_text": text,
            "lang": "en"
        }
    }

    # Add reply fields if present
    if in_reply_to_status_id:
        tweet["tweet"]["in_reply_to_status_id_str"] = in_reply_to_status_id
        tweet["tweet"]["in_reply_to_status_id"] = in_reply_to_status_id

    if in_reply_to_user_id:
        tweet["tweet"]["in_reply_to_user_id"] = in_reply_to_user_id
        tweet["tweet"]["in_reply_to_user_id_str"] = in_reply_to_user_id

    # Add retweet fields if present
    if retweeted_status_id:
        tweet["tweet"]["retweeted_status_id_str"] = retweeted_status_id
        tweet["tweet"]["retweeted_status_id"] = retweeted_status_id

    if retweeted_status_user_id:
        tweet["tweet"]["retweeted_status_user_id_str"] = retweeted_status_user_id
        tweet["tweet"]["retweeted_status_user_id"] = retweeted_status_user_id

    if retweeted_status_timestamp:
        tweet["tweet"]["retweeted_status_timestamp"] = parse_timestamp(retweeted_status_timestamp)

    return tweet


def convert_archive(input_dir, output_dir):
    """
    Convert a Grailbird archive to modern format.

    Args:
        input_dir: Path to Grailbird archive directory
        output_dir: Path to output directory (will be created)
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    # Check for tweets.csv
    csv_file = input_path / "tweets.csv"
    if not csv_file.exists():
        print(f"Error: {csv_file} not found")
        print(f"Expected Grailbird archive structure with tweets.csv in root")
        sys.exit(1)

    # Try to read account info from user_details.js
    user_details_path = input_path / "data" / "js" / "user_details.js"
    account_info = None

    if user_details_path.exists():
        print(f"Found user_details.js, extracting account info...")
        account_info = parse_user_details(user_details_path)

        if account_info:
            print(f"  Account: @{account_info['screen_name']} ({account_info['full_name']})")
            print(f"  ID: {account_info['id']}")
        else:
            print(f"  Warning: Could not parse user_details.js, using defaults")

    # Fallback to defaults if no user details found
    if account_info is None:
        print("Warning: No user_details.js found, using default account info")
        account_info = {
            'id': 'unknown',
            'screen_name': 'unknown',
            'full_name': 'Unknown User',
            'created_at': '2006-01-01 00:00:00 +0000'
        }

    print(f"\nReading tweets from {csv_file}...")

    # Read and convert tweets
    tweets = []
    try:
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tweet = convert_csv_to_tweet_object(row)
                tweets.append(tweet)
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        sys.exit(1)

    if not tweets:
        print("Warning: No tweets found in CSV file")
        sys.exit(1)

    print(f"Converted {len(tweets)} tweets")

    # Create output directory structure
    output_path.mkdir(parents=True, exist_ok=True)
    data_dir = output_path / "data"
    data_dir.mkdir(exist_ok=True)

    # Write tweets.js file
    tweets_file = data_dir / "tweets.js"
    print(f"Writing tweets to {tweets_file}...")

    try:
        with open(tweets_file, "w", encoding="utf-8") as f:
            f.write("window.YTD.tweets.part0 = ")
            json.dump(tweets, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error writing tweets file: {e}")
        sys.exit(1)

    # Convert created_at to ISO format for manifest
    created_dt = parse_grailbird_timestamp(account_info['created_at'])
    if created_dt:
        created_at_iso = created_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    else:
        created_at_iso = "2006-01-01T00:00:00.000Z"

    # Create manifest.js file
    manifest_file = data_dir / "manifest.js"
    manifest = {
        "userInfo": {
            "accountId": account_info['id'],
            "userName": account_info['screen_name'],
            "displayName": account_info['full_name']
        },
        "archiveInfo": {
            "sizeBytes": "0",
            "generationDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "isPartialArchive": False,
            "maxPartSizeBytes": "0"
        },
        "dataTypes": {
            "account": {
                "files": [
                    {
                        "fileName": "data/account.js",
                        "globalName": "YTD.account.part0",
                        "count": "1"
                    }
                ]
            },
            "tweets": {
                "files": [
                    {
                        "fileName": "data/tweets.js",
                        "globalName": "YTD.tweets.part0",
                        "count": str(len(tweets))
                    }
                ]
            }
        }
    }

    try:
        with open(manifest_file, "w", encoding="utf-8") as f:
            f.write("window.__THAR_CONFIG = ")
            json.dump(manifest, f, indent=2)
    except Exception as e:
        print(f"Error writing manifest file: {e}")
        sys.exit(1)

    # Create account.js file
    account_file = data_dir / "account.js"
    account_data = [{
        "account": {
            "accountId": account_info['id'],
            "username": account_info['screen_name'],
            "accountDisplayName": account_info['full_name'],
            "createdVia": "web",
            "createdAt": created_at_iso
        }
    }]

    try:
        with open(account_file, "w", encoding="utf-8") as f:
            f.write("window.YTD.account.part0 = ")
            json.dump(account_data, f, indent=2)
    except Exception as e:
        print(f"Error writing account file: {e}")
        sys.exit(1)

    print(f"\n✅ Conversion complete!")
    print(f"   Input: {input_path}")
    print(f"   Output: {output_path}")
    print(f"   Account: @{account_info['screen_name']} (ID: {account_info['id']})")
    print(f"   Total tweets: {len(tweets)}")
    print(f"\nTo import into tweetxvault, run:")
    print(f'   tweetxvault import x-archive "{output_path}"')


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    input_dir = sys.argv[1]
    output_dir = sys.argv[2]

    if not os.path.exists(input_dir):
        print(f"Error: Input directory '{input_dir}' does not exist")
        sys.exit(1)

    if os.path.exists(output_dir):
        print(f"Warning: Output directory '{output_dir}' already exists")
        response = input("Overwrite? (y/n): ")
        if response.lower() != "y":
            print("Cancelled")
            sys.exit(0)

        # Actually remove the directory to prevent stale files
        print(f"Removing existing directory...")
        shutil.rmtree(output_dir)

    convert_archive(input_dir, output_dir)


if __name__ == "__main__":
    main()
