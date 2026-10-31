# Offline recovery CI profile

The complete runner is `bash tests/run_offline.sh`. It needs Python 3.10 or
newer, Bash, ripgrep and the existing GNU shell utilities; the application has
no third-party runtime dependencies. It does not install packages. Run it in
the existing controller's isolated, credential-free source sandbox with a
read-only source mount and disposable writable temporary storage.

The runner discovers every Python unittest file, fails on errors, failures,
zero tests or skips, compiles every Python source, runs the five shell suites,
then runs the owned synthetic loopback CDP transport and CLI help/version
checks. The current proposed tree contains 149 unittest methods. Subtests are
not counted as additional methods. The transport script is an additional
smoke, not a unittest method.

The unit lane denies actual sockets and DNS. Recovery data, URL files and the
cookie database used by tests are generated synthetic fixtures. The transport
smoke owns an ephemeral loopback endpoint, restricts discovery to that one
endpoint, checks its child process's connection destinations, and requires its
listener and handler threads to retire. No browser/profile discovery is part
of that smoke. Production discovery and credential behavior are unchanged.

This is a source proposal for the existing controller; it creates no workflow,
controller, scheduler or status. A trusted controller must separately pin and
verify the repository, HEAD, base, synthetic merge tree, runner bytes, image
digest, test inventory, sandbox and final journal/cleanup before publishing
its existing App-bound status. A PR-controlled runner's success alone cannot
authorize that status or a merge.

In the source worker environment, all Python tests and five shell suites
passed, but the full runner stopped when creating the synthetic transport
socket returned `EPERM`. That operation was not retried. Native transport
acceptance, full-profile completion, native GUI/platform tests and live export
recovery remain separate gates. PR1 remains Draft with merge HOLD.
