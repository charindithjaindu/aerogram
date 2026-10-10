# Filters

Filters decide which updates reach which handler. They compose with `&`, `|`,
`~` exactly like Pyrogram.

```python
from aerogram import filters

@app.on_message(filters.text & ~filters.self)
async def texts(client, message): ...
```

## Built-in filters

| filter            | matches                                              |
|-------------------|------------------------------------------------------|
| `filters.text`    | has text content                                     |
| `filters.photo`   | photo message                                        |
| `filters.video`   | video message                                        |
| `filters.voice`   | voice note                                           |
| `filters.media`   | any media message                                    |
| `filters.self`    | sent by the logged-in account                        |
| `filters.incoming`| not from yourself (`~filters.self`)                  |
| `filters.private` | 1:1 thread                                           |
| `filters.group`   | group thread                                         |
| `filters.me`      | alias of `filters.self`                              |

## Parameterised filters

```python
filters.Chat("340282366841710301244259759389256700649")   # specific thread
filters.FromUser("12345678")                              # specific sender
filters.Regex(r"^/start(\s|$)")                           # regex on text
```

## Composing

```python
photo_or_video = filters.photo | filters.video
dm_from_friend = filters.private & filters.FromUser("12345678")

@app.on_message(dm_from_friend & ~filters.Regex(r"^ ?re:?"))
async def handler(client, message): ...
```

Negation: `~filters.self`. Everything evaluates against the `Message` object,
so `message.media`, `message.thread_id` etc. are all available.

## Custom filters

Any predicate works:

```python
long = filters.create(lambda m: len(m.text or "") > 200)

@app.on_message(long)
async def essay(client, message): ...
```

Or subclass `filters.Filter` and implement `__call__` for anything fancier
(the object receives the same `Message`).

## Handler groups

Like Pyrogram, each update is dispatched to **one handler per group**, in
group order:

```python
@app.on_message(filters.text, group=0)      # runs first
async def log_it(client, message): ...

@app.on_message(filters.text, group=1)      # also runs
async def reply_it(client, message): ...
```

If the first handler in a group raises, later groups still run - register
`@app.on_error` to see the exception.
