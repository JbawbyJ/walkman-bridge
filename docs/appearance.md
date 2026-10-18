# Red Lotus - Sony Walkman Dashboard appearance

Robert approved Paper & Ember as the default light appearance, Dark Bloom as a persistent Settings choice, and the visible name **Red Lotus - Sony Walkman Dashboard**. The supplied Lotus and four font files are bundled locally. The layout uses top navigation, flat panels, a persistent transport, and the existing playback/library/transfer workflows.

Appearance is saved through authenticated `GET/PATCH /api/preferences` in a separate `ui_preferences` table in the existing `nightops.sqlite`. A missing preference defaults to light. The desktop starts on a random loopback port, so browser localStorage cannot provide restart persistence. Failed saves leave the previous appearance selected and display a retryable error. Loading appearance failures do not prevent access to music.

The visible name changes in the renderer, native window, notifications and installer. The `bridge` product key, app ID, executable name, artifact filename, Electron partition, installation directory and `%LOCALAPPDATA%\Walkman Bridge` remain stable. Packaging test tools use a separate data-directory name.

Theme changes do not remount Workspace or usePlayer. Tests cover repeated switches with real browser audio, saved appearance reload, backend recreation across distinct origins, rejected and invalid writes, unchanged media/playback, small windows, dialogs, and rendered contrast in both themes. The native zoom matrix still depends on the host's screen work area; oversized cases must be reported as not run.

Desktop installation and physical-device validation are separate acceptance steps. Renderer screenshots and automated workflow tests use synthetic local fixtures.
