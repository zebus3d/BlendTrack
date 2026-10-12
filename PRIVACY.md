# Privacy Policy — BlendTrack

_Last updated: 2026-09-17_

BlendTrack is a desktop application that downloads and manages Blender
builds. It has **no user accounts, no telemetry and no analytics**. It does not
collect, store or share personal information.

## What the app stores on your computer

The app keeps its configuration and caches **locally**, on your own machine:

- **Settings** (`settings.json`): your preferences (destination folder, filters,
  language, window size, favorites, ...).
- **Cache** (`builds.json`, update caches and `blendtrack.log`): the last list
  of Blender builds, cached release information to avoid extra API calls, and a
  plain-text log used to diagnose errors.

These files live in the standard per-user folders of your operating system (for
example `%APPDATA%`/`%LOCALAPPDATA%` on Windows, `~/.config`/`~/.cache` on Linux,
`~/Library/...` on macOS). In *portable mode* (a `portable` marker next to the
executable) they are stored next to the executable instead. They never leave your
computer unless you copy them yourself.

## Network connections

The app connects only to download public Blender builds and to check for its own
updates:

- `builder.blender.org` and `cdn.builder.blender.org` — the list of Blender builds
  and the builds themselves (Blender Foundation).
- `download.blender.org` — official Blender releases (Blender Foundation).
- `api.github.com` and GitHub release assets (`github.com`,
  `release-assets.githubusercontent.com`) — to check for and download updates of
  BlendTrack itself (GitHub), and the UPBGE fork builds if you enable them.
- `cloud.bforartists.de` — the Bforartists fork builds, if you enable that fork
  in Settings. Requests use the project's **public** WebDAV share, with no
  account of yours.
- Release notes pages on `developer.blender.org`, and the Bforartists/UPBGE
  sites, only when you open them.

These requests carry the standard HTTP information any web request includes (your
IP address and a `User-Agent` identifying the app); no personal data is added. The
app does not send analytics, usage statistics or crash reports. Please refer to
Blender Foundation's and GitHub's own privacy policies for how they handle
requests to their servers.

## Launching Blender and opening links

When you ask it to, the app launches a Blender build you downloaded and opens
URLs or folders with your system's default applications. That is a local action;
Blender itself is a separate program with its own behavior.

## Uninstalling

Delete the application files. To remove everything, also delete the settings and
cache folders listed above (or the app folder in portable mode). Blender builds
are uninstalled from within the app.

## Contact

This is an open-source project. For any question about this policy, open an issue
at <https://github.com/zebus3d/BlendTrack/issues>.
