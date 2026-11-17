# Phase 0 self-hosted image build runner

The project owner registers the Apple silicon macOS workstation as a temporary
repository runner. Follow GitHub's [add a self-hosted runner instructions](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)
for this repository and operating system, and apply the custom label
`nextoomio-build`. Use GitHub's current registration instructions and token;
do not place the token in the repository or logs. Start Colima with Docker
available before starting the runner, and keep both running for full builds.
The workflow uses the persistent Docker volume `nextoomio-ci-build` for the
case-sensitive build workspace, downloads, and sstate. Pull request jobs run on
GitHub-hosted runners only. Full builds have a 180-minute timeout.

After Phase 0, stop the runner and follow GitHub's [remove a self-hosted runner instructions](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/remove-runners)
to remove its repository registration. The Phase 1 dedicated runner is a
separate decision; do not remove the persistent volume as part of this procedure.
