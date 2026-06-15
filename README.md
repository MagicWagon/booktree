# booktree
Reorganize your audiobooks using ID3 or Audible metadata into a tree structure recommended and supported by media servers like Audibookshelf. The originals are untouched and will be hardlinked to their destination

It does the following:
- take a source folder, ideally your downloads folder where your audiobook files are
- recursively find all the M4B/MP3 files in it, and for each file:
  - pull and parse metadata information from id3 tags
  - using the id3 tags and the file information, attempt to pull metadata from the Metadata sources
  - create a tree structure on the target folder, ideally your media folder (like your abs audiobook library folder)
  - hardlink the audiobook file to the target folder

<mark>booktree</mark> builds the following heirarchy on the target folder (this is configurable):
* <media_path>/Author/Title (If there is no series information)
* <media_path>/Author/Series/Series #Part - Title

The above format is the default. User can modify/tweak this in the config file.  See [Config File Documentation](CONFIG.md)

## Usage:
### Help
~~~
to run in the docker container, run: > docker exec [-it] <<container_name>> /venv/bin/python booktree.py /config/<<config>>.json 

usage: booktree [-h] [--dry-run] config_file

Reorganize your audiobooks using ID3 or Audbile metadata. The originals are untouched and will be hardlinked to their
destination

positional arguments:
  config_file           Your Config File

options:
  -h, --help            show this help message and exit
  --dry-run             If provided, will override dryRun in config
~~~

## Install
* Python >= 3.10
* ffmpeg
* httpx
* thefuzz 
* pathvalidate
* Requests
* langcodes

1. run pip install -r requirements.txt to install dependencies
2. copy default_config.cfg into config.json and modify with your paths settings (files, source_path, media_path)
3. if using MAM as a source, create a MAM session ID and set the value in config.json file (/Config/session)

## Web UI

The Docker image now starts a Next.js web UI on port `3000`. The UI is for exception handling: review books that need metadata or matching, edit fields, run targeted MAM/Audible searches, accept a candidate, and reprocess one book at a time.

Operational state is stored in SQLite at:

~~~
/config/booktree.db
~~~

Normal CLI runs write their processed book state directly to this database after the CSV log is written. Existing CSV logs can still be synced from the UI for backfill or recovery.

The existing CLI still works inside the container:

~~~
docker exec -it booktree /venv/bin/python booktree.py /config/config.json
~~~

To expose the UI in Docker Compose:

~~~
services:
  booktree:
    image: ghcr.io/magicwagon/booktree:latest
    ports:
      - "3000:3000"
    volumes:
      - /path/to/booktree/config:/config
      - /path/to/booktree/logs:/logs
      - /path/to/media:/data
~~~

If Booktree is behind Gluetun, expose the port on the Gluetun service instead and keep Booktree using `network_mode: "service:gluetun"`.

### Mousehole Integration

booktree can use [mousehole](https://github.com/t-mart/mousehole) to read the current MAM session cookie from mousehole's `state.json` file. This avoids manually updating `/Config/session` when your IP address changes and mousehole rotates the cookie.

Enable mousehole in your config:

~~~
"mousehole_enabled": 1,
"mousehole_state_file": "/app/secrets/state.json"
~~~

The mousehole state file should contain a URL-encoded `currentCookie` value:

~~~
{
    "currentCookie": "your%2Bmam%2Fsession%2Fcookie"
}
~~~

When mousehole is enabled, booktree uses `currentCookie` directly and ignores any cached `cookies.pkl` so token rotation is picked up immediately. If the state file is missing or invalid, booktree falls back to `/Config/session`.

For Docker, mount the same mousehole data directory into the booktree container and point `mousehole_state_file` at the mounted state file:

~~~
services:
  mousehole:
    image: tmmrtn/mousehole:latest
    volumes:
      - /path/to/mousehole:/srv/mousehole

  booktree:
    volumes:
      - /path/to/mousehole:/app/secrets
      - /path/to/booktree/config:/config
      - /path/to/logs:/logs
~~~

### Recommended Workflow

1. Start small (pick a folder that has a handful of books, don't run it on 2K files the first try :) )
2. Run <mark>booktree</mark> in <mark>--dry-run</mark> mode
3. Check the resulting log file to check the matches.  What you should check for:
    * Rows where isMatched = TRUE
      * Anywhere mamCount = 1 is an exact match... celebrate!
      * Check for rows where mamCount or audibleMatchCount is high (>3), if it is, just check if it picked the right match
    * Rows where isMatched = FALSE - there are many reasons why there won't be a match
      *  The book is NOT SOLD on Audible at all (or in your region)
      *  The book/torrent has been deleted since you snatched it
      *  The ID3 metadata is empty or bad, e.g., Author/Narrator that's not comma delimited, bad title and series information
4.  If everything looks good, rerun booktree without the --dry-run parameter
5.  Recategorize/Set Location (in you client, e.g., Qbit), to where you have your "processed" files to optimize performance. It's ok if you don't, the script will add them to the list of files to be processed, but will skip processing them if they have already been processed before (cache check).

  Optionally, you can choose to work on the log file, and feed that as input to booktree in a succeeding run:

1. Fix the <mark>paths</mark> column to edit/change the generated target path.  When isMatched=TRUE, booktree will just use the paths value as-is
2. If isMatched = FALSE, you can fix the id3-metadata to re-do the search.  The areas to focus on are:
    *  id3-asin
    *  id3-title
    *  id3-author
    *  id3-seriesparts
3. Rerun booktree using the "log" mode and passing the updated logfile as input, booktree.py log /config/log_config.json. I recommend having a separate log_config.json file for this 

## Disclaimers

* It should work seamlessly on any single file or multi-file book under a single book folder
* The script may not immediately work on older, multibook collections >> set multibook = true
* The script may not immediately work on Multi-CD books
* Hard linking will only work if the source and target paths are on the same volume.  If you are using Unraid, same datasets

## FAQ
  **Q:  Where is my config file?**
  <p>A: You can copy the default_config.cfg into <somefile>.json.  Modify or add the values of paths: [{file, source_path, media_path}]</p>

  **Q:  My files are from other sources, can I still use this tool?**
  <p>A: Use audible as metadata source, Config/metadata = audible</p>

  **Q:  What if the mam or audible search returns multiple matches?**
  <p>A: Fuzzymatch is used to get the best match</p>

  **Q:  My metadata is not producing any match, what can I do?**
  <p>A: Lower the matchrate, Change the fuzzy_match algorith, Set --fixid3 flag.</p>
  
