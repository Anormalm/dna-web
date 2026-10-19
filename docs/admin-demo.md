# Admin workflow demonstration

Open `/admin-demo`, or choose **Try workflow demo** on `/admin`.
The existing private preview retains its Site access policy. This route needs no
GitHub session because it contains only browser-local example data; live admin
APIs retain their existing GitHub, origin and CSRF checks.

1. Choose **Open-weight example** or **API / BYOK example**.
2. **Inspect example** shows fixed illustrative resource, duration and cost figures.
   Editing the model ID does not fetch real metadata or change those figures.
3. **Add mock job**, then **Start**. Progress is accelerated to about 25 seconds.
4. **Stop** retains the simulated responses; **Start** resumes the same phase.
5. For an API budget demonstration, set the mock ceiling to `$0.40` before
   starting. The job pauses when its next simulated block would exceed the cap.
   Increase the ceiling and click **Start** to resume.
6. Local completion illustrates temporary weight cleanup. API completion displays
   simulated usage. Neither publishes to the real Galaxy.
7. **Reset demo** or refreshing clears every example job and restores `$25`.

No credentials, quotes, budgets or jobs are sent to the backend. The simulation
imports no live admin API code, writes no persistent storage, and has no provider
or model-download clients. Example costs are not real pricing estimates. The
shared application shell may load its usual static catalog assets.

Validation: `npm run typecheck:admin`,
`npx vitest run test/admin-demo.test.ts test/admin.test.ts`, `npm run build`.
Browser QA covers local/API workflows, pause/resume, budget exhaustion, cleanup,
reset, mobile layout, and zero `/api/` requests while using the demo.
