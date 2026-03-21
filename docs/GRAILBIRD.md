# Converting Old "Grailbird" Twitter Archives

## Background

Twitter used a different archive format (known as "Grailbird") for exports before approximately 2018. If you have old Twitter archives from 2017 or earlier, they may use this older format and cannot be directly imported into tweetxvault.

### How to Identify a Grailbird Archive

Grailbird archives have this structure:
```
TwitterArchive/
├── tweets.csv                  # All tweets in CSV format
├── index.html                  # Viewer interface
├── data/
│   └── js/
│       ├── user_details.js
│       ├── tweet_index.js
│       └── tweets/
│           ├── 2007_01.js     # Monthly tweet files
│           ├── 2007_02.js
│           └── ...
```

Modern archives (post-2018) have this structure:
```
twitter-archive/
├── data/
│   ├── manifest.js            # Contains window.__THAR_CONFIG
│   ├── account.js
│   ├── tweets.js              # window.YTD.tweets.part0
│   └── ...
```

## Converting Grailbird Archives

Use the `convert_grailbird.py` script to convert old archives to the modern format:

```bash
python convert_grailbird.py <input_dir> <output_dir>
```

### Example

```bash
# Convert the archive
python convert_grailbird.py TwitterArchive-2015 TwitterArchive-2015-converted

# Import into tweetxvault
tweetxvault import x-archive TwitterArchive-2015-converted
```

## What Gets Converted

The converter reads `tweets.csv` and `data/js/user_details.js` (if present) to create a modern archive structure containing:

- **Account metadata**: Screen name, display name, user ID, account creation date (from user_details.js)
- **Tweet text and metadata**: Full text, timestamps, tweet IDs
- **Reply information**: In-reply-to status and user IDs
- **Retweet information**: Retweeted status IDs, user IDs, and timestamps
- **URLs**: Expanded URLs from tweets
- **Proper timestamp format**: Converts from `"2015-01-10 20:59:42 +0000"` to Twitter's format

## Limitations

- **Likes/favorites**: Not included in Grailbird archives, only authored tweets
- **Media files**: Not converted (Grailbird archives do not include media in the CSV)
- **User mentions**: Basic entity structure created but @mentions not parsed from tweet text
- **Hashtags**: Basic entity structure created but #hashtags not parsed from tweet text
- **No user_details.js**: If the archive lacks user_details.js, account metadata defaults to "unknown"

## Why This Matters

Many early Twitter users (2006-2010) have used services like tweetdelete.net to automatically delete old tweets. Grailbird archives from 2015-2017 may be the only surviving record of those early tweets. For archival and personal history projects, recovering this data is essential.

## Technical Details

The converter:
1. Reads `tweets.csv` from the Grailbird archive
2. Parses `data/js/user_details.js` using JSON parsing (handles minified, trailing-comma, and standard formatting)
3. Transforms each row into the modern tweet object format
4. Preserves URLs correctly even when they contain commas (using regex lookahead split)
5. Generates required files: `manifest.js`, `tweets.js`, `account.js`
6. Creates proper `window.__THAR_CONFIG` and `window.YTD.tweets.part0` structure

The resulting archive can be imported using tweetxvault's standard `import x-archive` command.

### Testing

Run the included unit tests:
```bash
python3 -m unittest test_convert_grailbird.py
```

Tests cover various `user_details.js` formatting variants and URL patterns.
