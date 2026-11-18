#!/usr/bin/env python3
"""Read Reddit through Arctic Shift using only the Python standard library."""
import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://arctic-shift.photon-reddit.com/api"
POST_FIELDS = ("id", "title", "selftext", "permalink", "url", "created_utc",
               "score", "num_comments", "author", "subreddit")


def post_id(value):
    value = value.strip()
    if "://" in value:
        url = urllib.parse.urlparse(value)
        if url.hostname == "redd.it":
            value = url.path.strip("/")
        elif url.hostname in ("reddit.com", "www.reddit.com", "old.reddit.com", "np.reddit.com"):
            match = re.search(r"/comments/([a-zA-Z0-9]+)(?:/|$)", url.path)
            if not match:
                raise ValueError("Use a full Reddit comments URL, not a share link")
            value = match.group(1)
        else:
            raise ValueError("Expected a Reddit post URL")
    if value.startswith("t3_"):
        value = value[3:]
    if not re.fullmatch(r"[a-zA-Z0-9]+", value):
        raise ValueError("Expected a bare post ID or Reddit post URL")
    return value.lower()


def rank_posts(posts, limit):
    return sorted(posts, key=lambda p: p.get("score") or 0, reverse=True)[:limit]


class Client:
    def __init__(self):
        self.last_call = None

    def get(self, path, **params):
        url = BASE + path + "?" + urllib.parse.urlencode(params)
        for attempt in range(4):
            if self.last_call is not None:
                time.sleep(max(0, 2 - (time.monotonic() - self.last_call)))
            self.last_call = time.monotonic()
            request = urllib.request.Request(url, headers={
                "User-Agent": "reddit-reading/1.0 (community archive reader)",
                "Accept": "application/json",
            })
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    message = payload.get("error", "Invalid archive response") if isinstance(payload, dict) else "Invalid archive response"
                    raise RuntimeError(message)
                return payload["data"]
            except urllib.error.HTTPError as error:
                if error.code not in (422, 429) and not 500 <= error.code <= 599:
                    raise RuntimeError("Archive HTTP %s: %s" % (error.code, error.read(1000).decode(errors="replace"))) from error
                if attempt == 3:
                    raise RuntimeError("Archive HTTP %s after four attempts" % error.code) from error
                delay = 2 ** (attempt + 1)
                for header in ("Retry-After", "X-RateLimit-Reset"):
                    try:
                        reset = float(error.headers.get(header, 0))
                        if math.isfinite(reset):
                            delay = max(delay, reset)
                    except (ValueError, TypeError):
                        pass
                print("Archive HTTP %s; retry in %ss" % (error.code, delay), file=sys.stderr)
                time.sleep(delay)
        raise RuntimeError("Archive request failed")


def compact(post):
    result = {key: post.get(key) for key in POST_FIELDS}
    result["permalink"] = post.get("permalink") or "/comments/%s/" % post["id"]
    return result


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def parser():
    root = Parser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    top = commands.add_parser("top", help="rank the newest 100 archived posts by score")
    top.add_argument("sub")
    search = commands.add_parser("search", help="keyword search within a subreddit")
    search.add_argument("query")
    search.add_argument("--sub", required=True, help="required by the archive keyword API")
    user = commands.add_parser("user", help="recent archived posts by author")
    user.add_argument("name")
    for command in (top, search, user):
        command.add_argument("--days", type=positive, default=7)
        command.add_argument("--limit", type=positive, default=15)
    post = commands.add_parser("post", help="one post with an archived comment tree")
    post.add_argument("id_or_url")
    post.add_argument("--comments", type=int, default=50, help="0 skips comments; maximum 25000")
    return root


def run(args, client):
    if args.command == "post":
        if not 0 <= args.comments <= 25000:
            raise ValueError("--comments must be between 0 and 25000")
        identifier = post_id(args.id_or_url)
        posts = client.get("/posts/ids", ids=identifier)
        if not posts:
            raise RuntimeError("Post not found in archive")
        tree = client.get("/comments/tree", link_id=identifier, limit=args.comments) if args.comments else []
        return {"source": "arctic-shift", "post": compact(posts[0]), "comments": tree}
    if args.limit > 100:
        raise ValueError("--limit must be between 1 and 100")
    params = {"after": int(time.time()) - args.days * 86400, "sort": "desc",
              "limit": 100 if args.command == "top" else args.limit}
    if args.command == "user":
        params["author"] = args.name
    else:
        params["subreddit"] = args.sub
    if args.command == "search":
        params["query"] = args.query
    posts = client.get("/posts/search", **params)
    output = rank_posts(posts, args.limit) if args.command == "top" else posts
    return {"source": "arctic-shift", "mode": args.command, "filters": params,
            "sample_size": len(posts), "sample_may_be_truncated": len(posts) == params["limit"],
            "ranking_scope": "newest 100 archived posts in window" if args.command == "top" else "newest matches",
            "posts": [compact(post) for post in output]}


def main():
    try:
        result = run(parser().parse_args(), Client())
    except (ValueError, RuntimeError, OSError) as error:
        print(json.dumps({"error": str(error)}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
