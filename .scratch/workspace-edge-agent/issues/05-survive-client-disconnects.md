# 05: Keep the agent available across client disconnects

**What to build:** An operator starts the agent as a background service, disconnects the development client, and later resumes job inspection without restarting or duplicating accepted work.

**Blocked by:** 04: Run development commands in the project environment.

**Status:** ready-for-agent

- [ ] Provide reproducible install, start, stop, and startup-failure instructions for one documented Edge operating system; the agent operates without a browser session.
- [ ] A long-running job continues after client disconnect and a new authorized client resumes status and output from its job identifier and output cursor.
- [ ] Persist accepted mutation request identities and job records on the Edge; retrying an identical request returns its original outcome or job, and conflicting reuse is rejected.
- [ ] Demonstrate reconnecting after a lost start acknowledgement without applying an edit twice or launching a second command.
- [ ] Completed jobs remain inspectable after agent restart; interrupted jobs are identified honestly and no orphaned managed process is silently left running.
- [ ] Cover disconnect, duplicate delivery, restart recovery, and shutdown behavior using real processes through the agent entry point.
