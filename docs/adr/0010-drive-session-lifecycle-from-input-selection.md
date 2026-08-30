# Drive Session lifecycle from Input selection

Choosing a Playback Source in the always-visible Inputs section is the user's explicit request to start routing immediately; changing a non-empty selection applies the latest complete set through a bounded stop/start transition, and removing the final Source ends and cleans the Session. This replaces a separate generic Start control because splitting selection from activation made Source changes easy to leave stopped and obscured the primary routing controls.

Capture Sources retain a stronger boundary: choosing one presents an additional per-Session confirmation before any start or restart that includes microphone audio. **End Stream** clears the current selection and performs the same ownership-safe cleanup, while Pin, Recently used, Refresh, panel opening, profile loading, wake, unlock, login, and reconnection never start audio.
