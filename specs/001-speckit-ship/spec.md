# Feature Specification: SpecKit Ship

**Feature Branch**: `001-speckit-ship`

**Created**: 2026-08-12

**Status**: Draft

**Input**: User description: "i would like a new /speckit-ship skill and community extension. to be in line with our workflow meaning. commit the local branch, push to github, pr into main, watch ci and then on green deploy to production. on red fix the merge conflicts or build errors. finally prune the branch swap to main and then pull remote main to local. this would need to be repo agnostic so would need to 1. figure out if it has a git repo 2. figure out what its main or production or parable branch. 3. it would also be really cool to add an extension to speckit companion to add a ship tab with github stats from a local json file like the other companion tasks etc. it could show whats committed, whats behind, changelog, whats deployed whats pr'ed where etc. maybe this is where we can configure the from to branch and configure comments or even ai generated comments etc. see CI / CD status. This would be a secondary extension maybe for /speckit-community-ship"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ship the current branch end to end (Priority: P1)

A developer has finished work on a feature branch and wants it live. They run a single ship command. The tool figures out where it is, confirms what it is about to do, commits any outstanding work on the branch, publishes the branch to the hosting service, opens a pull request against the repository's integration branch, waits for the automated checks to finish, and — once they are green and the developer has approved the merge — merges the pull request, triggers or confirms the production release, deletes the now-merged branch locally and remotely, switches the working copy back to the integration branch, and pulls the latest remote state. At the end the developer is on an up-to-date integration branch with their work released, and they never had to remember the sequence.

**Why this priority**: This is the entire reason the feature exists. Without it there is no product. Every other story either protects this path or reports on it.

**Independent Test**: Can be fully tested by taking a repository with a clean feature branch containing one commit's worth of work, running the ship command, and confirming that the pull request was created and merged, the release completed, the branch is gone locally and remotely, and the working copy sits on an up-to-date integration branch. Delivers the complete value of the feature on its own.

**Acceptance Scenarios**:

1. **Given** a repository on a feature branch with uncommitted changes and a configured remote, **When** the developer runs the ship command and approves each gate, **Then** the changes are committed, the branch is pushed, a pull request into the integration branch is opened, and the run proceeds to the checks stage.
2. **Given** an open pull request whose automated checks have all reported success, **When** the developer approves the merge gate, **Then** the pull request is merged, the release step runs, and the run reports the released state.
3. **Given** a successfully merged and released pull request, **When** the cleanup stage runs, **Then** the feature branch is deleted locally and on the remote, the working copy is switched to the integration branch, and the integration branch is updated from the remote.
4. **Given** a feature branch with no changes to commit that is already identical to the integration branch, **When** the developer runs the ship command, **Then** the run stops before creating a pull request and reports that there is nothing to ship.
5. **Given** a run that has already opened a pull request, **When** the developer runs the ship command again on the same branch, **Then** the run adopts the existing pull request rather than creating a duplicate and resumes from the checks stage.

---

### User Story 2 - Recover from a red pipeline (Priority: P2)

The automated checks fail, or the branch cannot be merged because it conflicts with the integration branch. Instead of dropping the developer back to a bare shell, the ship run diagnoses which kind of failure occurred, retrieves the failure detail, attempts a bounded set of repairs — resolving conflicts by bringing the branch up to date with the integration branch, or correcting build and test failures — pushes the repair, and re-enters the checks stage. If the repairs do not clear the failure within the attempt budget, the run stops and hands the developer a precise account of what failed, what was tried, and what is left.

**Why this priority**: Red pipelines are the normal case often enough that a happy-path-only tool gets abandoned. But the happy path has to exist first, so this is P2.

**Independent Test**: Can be tested by shipping a branch with a deliberately broken test and a separate branch that conflicts with the integration branch, and confirming in each case that the run detects the failure class, attempts repair, and either re-enters the checks stage or halts with a specific, actionable report. Delivers value independently because it turns a failed ship into a diagnosed ship.

**Acceptance Scenarios**:

1. **Given** a pull request whose checks report failure, **When** the checks stage completes, **Then** the run retrieves the failing check's output, classifies the failure, and reports the classification before attempting any repair.
2. **Given** a branch that cannot merge cleanly into the integration branch, **When** the run reaches the merge stage, **Then** the conflict is detected before merging, the branch is brought up to date with the integration branch, conflicts are resolved, and the checks stage is re-entered.
3. **Given** a failure that the run cannot repair within its attempt budget, **When** the budget is exhausted, **Then** the run halts, leaves the branch and pull request intact, and reports each attempt made and the remaining failure.
4. **Given** a repair the run is not permitted to make unattended, **When** the repair is identified, **Then** the run describes the proposed change and waits for the developer instead of applying it.
5. **Given** a checks stage that has produced no result within the configured wait limit, **When** the limit is reached, **Then** the run stops waiting, reports the checks as unresolved rather than as passing or failing, and does not merge.

---

### User Story 3 - Ship from an unfamiliar repository (Priority: P2)

A developer runs the ship command in a repository the tool has never seen. Before touching anything, the run establishes the ground truth: that this is a version-controlled repository at all, which branch is the integration target, which remote to publish to, whether the hosting service is reachable and authenticated, and whether the repository has automated checks and a release path. Anything it cannot determine, it asks about once and remembers. Anything that makes the run unsafe, it refuses on, with the reason stated.

**Why this priority**: "Repo agnostic" is an explicit requirement, and a wrong guess about the integration branch is the single most damaging error this tool can make. It ranks alongside failure recovery.

**Independent Test**: Can be tested by running the preflight stage alone against several repositories — one with a `main` integration branch, one with a differently named integration branch, one with no remote, and one directory that is not a repository — and confirming the detected profile is correct in each case and the refusal is clear in the last. Delivers value independently as a "can I ship from here, and where would it go?" check.

**Acceptance Scenarios**:

1. **Given** a directory that is not inside a version-controlled repository, **When** the ship command runs, **Then** it stops immediately, states that no repository was found, and makes no changes.
2. **Given** a repository whose integration branch is not named `main`, **When** the preflight stage runs, **Then** the actual integration branch is detected from the repository's own configuration rather than assumed.
3. **Given** a repository where the integration branch cannot be determined unambiguously, **When** the preflight stage runs, **Then** the run presents the candidates it found, asks the developer to choose, and records the choice for subsequent runs.
4. **Given** a repository with no remote configured or with unusable credentials for the hosting service, **When** the preflight stage runs, **Then** the run stops before committing anything and states which precondition failed.
5. **Given** the working copy is already on the integration branch, **When** the ship command runs, **Then** the run refuses to open a pull request from the integration branch into itself and explains what it expected instead.

---

### User Story 4 - See ship state at a glance (Priority: P3)

A developer opens a Ship tab alongside the existing SpecKit Companion views. Without leaving the editor they can see the state of the current repository's ship pipeline: what is committed locally and not yet published, how far behind the integration branch the working copy is, which pull requests are open and where they point, the outcome of the most recent automated checks, what was most recently released and when, and a changelog of recent ship runs. Every value on the tab carries the moment it was captured, and anything the tool could not determine is shown as undetermined rather than filled in.

**Why this priority**: Reporting is genuinely useful but strictly downstream of the pipeline that produces the data. The pipeline is usable without the tab; the tab is empty without the pipeline.

**Independent Test**: Can be tested by pointing the tab at a repository with a recorded ship history and confirming each panel reflects the recorded state, and by pointing it at a repository with no history and confirming it shows an honest empty state. Delivers value independently as a read-only status view.

**Acceptance Scenarios**:

1. **Given** a repository with a recorded ship history, **When** the developer opens the Ship tab, **Then** the local, published, pull-request, checks, and release panels each show the recorded state together with the time that state was captured.
2. **Given** a repository with no recorded ship history, **When** the developer opens the Ship tab, **Then** each panel shows an explicit empty state rather than placeholder or sample values.
3. **Given** a piece of state the tool could not determine, **When** the tab renders that panel, **Then** the value is shown as undetermined with the reason, and is never substituted with a default or a guess.
4. **Given** a ship run in progress, **When** the run advances a stage, **Then** the tab reflects the new stage without the developer restarting the editor.
5. **Given** recorded state older than the configured freshness window, **When** the tab renders, **Then** it marks that state as stale and shows how to refresh it.

---

### User Story 5 - Configure ship behavior from the tab (Priority: P3)

From the same Ship tab, a developer sets the repository's ship configuration: which branch ships into which, how the pull request title and body are composed — written by hand, assembled from the branch's commits, or drafted automatically — and what the release step means for this repository. Settings are stored with the repository, take effect on the next run, and any control whose backing behavior is not available is shown disabled with the reason rather than shown as working.

**Why this priority**: Configuration has a working default (detection plus prompting, per User Story 3), so the tab's editing surface is a convenience layer on top of an already-functional pipeline.

**Independent Test**: Can be tested by changing the target branch and the pull request composition mode in the tab, then running a ship and confirming the run honors both settings. Delivers value independently as the discoverable front end to settings that would otherwise be edited by hand.

**Acceptance Scenarios**:

1. **Given** the Ship tab is open, **When** the developer changes the source or target branch and saves, **Then** the next ship run in that repository uses the saved branches.
2. **Given** the developer selects automatic drafting of the pull request description, **When** a ship run opens a pull request, **Then** the description is drafted from the branch's actual changes and presented for review before the pull request is created.
3. **Given** a configuration control whose underlying capability is unavailable in this repository, **When** the tab renders, **Then** that control is shown disabled with the reason stated and cannot be toggled.
4. **Given** an invalid configuration, such as a target branch that does not exist, **When** the developer saves, **Then** the save is rejected with the specific problem named and the previous configuration is retained.

---

### Edge Cases

- The working copy is in a detached-head state, mid-rebase, or mid-merge when the ship command starts.
- The working copy contains changes unrelated to the feature being shipped, so committing everything would ship more than intended.
- The repository has several remotes, or the branch tracks a remote other than the one the pull request should target.
- A pull request for this branch already exists, is closed, or was merged in a previous run.
- The repository's rules require an approving review, a signed commit, or a linear history that the branch does not satisfy.
- The repository has no automated checks configured at all, so there is no green to wait for.
- Automated checks never report, report after an unusually long delay, or report a mix of required and optional failures.
- The merge succeeds but the release step fails, leaving the integration branch ahead of production.
- The repository has no release path at all, or has one the tool cannot classify as observed or executed.
- The repository releases on merge but the release is queued behind other releases, so confirmation arrives long after the merge.
- The branch cannot be deleted because the hosting service protects it or another open pull request depends on it.
- Two ship runs are started against the same repository or the same branch at the same time.
- The repository is a monorepo where a single integration branch feeds more than one release target.
- The developer interrupts the run partway through, leaving a published branch and an open pull request behind.
- The recorded ship state file is missing, unreadable, or was written by an incompatible version.
- The repository is private, the credentials in use lack permission for one of the stages, or credentials expire mid-run.

## Requirements *(mandatory)*

### Functional Requirements

**Repository discovery and preflight**

- **FR-001**: System MUST determine whether the current working directory is inside a version-controlled repository before taking any action, and MUST stop with a stated reason when it is not.
- **FR-002**: System MUST determine the repository's integration branch from the repository's own configuration rather than assuming a fixed name, and MUST support integration branches under any name.
- **FR-003**: System MUST ask the developer to choose when the integration branch cannot be determined unambiguously, and MUST record the choice so subsequent runs in that repository do not ask again.
- **FR-004**: System MUST verify, before committing or publishing anything, that a usable remote exists, that the hosting service is reachable, and that the credentials in use permit the stages the run intends to perform.
- **FR-005**: System MUST refuse to run when the working copy is on the integration branch itself, in a detached-head state, or in the middle of an unfinished rebase or merge, and MUST state which condition blocked it.
- **FR-006**: System MUST present a preflight summary of the detected repository profile and the actions it intends to take, before performing the first action that changes any state.

**Ship pipeline**

- **FR-007**: System MUST stage and commit outstanding work on the current branch, and MUST show the developer what will be committed before committing it.
- **FR-008**: System MUST publish the current branch to the configured remote, establishing tracking when the branch has not been published before.
- **FR-009**: System MUST open a pull request from the current branch into the configured target branch, and MUST adopt an existing open pull request for that branch instead of creating a duplicate.
- **FR-010**: System MUST compose the pull request title and description according to the repository's configured composition mode, and MUST present a machine-drafted description for the developer's review before the pull request is created.
- **FR-011**: System MUST wait for the pull request's automated checks to reach a terminal outcome, MUST report progress while waiting, and MUST stop waiting at a configured limit rather than waiting indefinitely.
- **FR-012**: System MUST treat a checks outcome it could not determine as unresolved, and MUST NOT proceed to merge on an unresolved outcome.
- **FR-013**: System MUST obtain the developer's explicit confirmation before merging the pull request and before performing the release, on every run, unless the developer has authorized the run to proceed unattended for that specific run.
- **FR-014**: System MUST perform the release step only after the pull request has merged, and MUST report the release outcome as a distinct result from the merge outcome.
- **FR-015**: System MUST NOT report a release as complete based on the merge alone; a release is complete only when the release path itself has confirmed it.

**Failure handling**

- **FR-016**: System MUST classify a failed run as a merge conflict, a check failure, a permission or precondition failure, or an unresolved outcome, and MUST report the classification.
- **FR-017**: System MUST retrieve and present the failing check's own output rather than reporting only that a check failed.
- **FR-018**: System MUST detect that the branch cannot merge cleanly before attempting a merge, and MUST bring the branch up to date with the target branch and resolve the conflict before re-entering the checks stage.
- **FR-019**: System MUST attempt repairs for classified failures within a bounded number of attempts, MUST re-enter the checks stage after each repair, and MUST halt when the budget is exhausted.
- **FR-020**: System MUST leave the branch and pull request intact when it halts, and MUST report every repair attempted and the failure that remains.
- **FR-021**: System MUST be resumable: re-running the command against a branch with a run already in progress MUST continue from the furthest stage reached rather than starting over or duplicating work.
- **FR-022**: System MUST detect a ship run already in progress against the same branch and MUST refuse to start a second concurrent run.

**Cleanup**

- **FR-023**: System MUST delete the shipped branch locally and on the remote only after confirming the pull request merged, and MUST report rather than fail the run when the remote refuses the deletion.
- **FR-024**: System MUST switch the working copy to the integration branch and update it from the remote as the final stage of a successful run.
- **FR-025**: System MUST NOT delete any branch that has unmerged commits, and MUST report the unmerged commits instead.

**Recorded ship state**

- **FR-026**: System MUST record each ship run's stages, outcomes, and timings to a per-repository state file that the Companion Ship tab reads.
- **FR-027**: System MUST stamp every recorded value with the time it was captured.
- **FR-028**: System MUST record an explicit undetermined marker, with a reason, for any state it could not establish, and MUST NOT record a default or inferred value in its place.
- **FR-029**: System MUST tolerate a missing, unreadable, or version-incompatible state file by reporting the condition and continuing, and MUST NOT abort a ship run because of it.

**Companion Ship tab**

- **FR-030**: Ship tab MUST display, for the current repository: work committed locally but not published, how far the working copy is behind the integration branch, open pull requests and their source and target branches, the most recent automated-check outcome, the most recent release and its time, and a changelog of recent ship runs.
- **FR-031**: Ship tab MUST show the capture time alongside every displayed value.
- **FR-032**: Ship tab MUST render undetermined state as undetermined, with the recorded reason, and MUST NOT substitute a placeholder, sample, or default value for it.
- **FR-033**: Ship tab MUST show an explicit empty state, distinguishable from a zero value, when no ship history exists for the repository.
- **FR-034**: Ship tab MUST mark displayed state as stale once it is older than the configured freshness window, and MUST offer a way to refresh it.
- **FR-035**: Ship tab MUST reflect stage changes from a run in progress without requiring the editor to be restarted.
- **FR-036**: Ship tab MUST render any control whose backing capability is unavailable as visibly disabled with the reason stated, and MUST NOT present such a control as operable.

**Ship configuration**

- **FR-037**: Developers MUST be able to set the source and target branch for a repository's ship runs, and runs MUST honor the saved values.
- **FR-038**: Developers MUST be able to choose how the pull request title and description are composed: entered manually, assembled from the branch's commits, or drafted automatically.
- **FR-039**: Developers MUST be able to define what the release step means for the repository, choosing between an *observed* release — performed by the repository's own automation once the merge lands, with the run watching until it confirms — and an *executed* release — a release action the run performs itself after the merge.
- **FR-040**: System MUST validate configuration on save, MUST reject an invalid configuration with the specific problem named, and MUST retain the previous configuration when a save is rejected.
- **FR-041**: System MUST store ship configuration per repository so that settings travel with the repository rather than with the developer's machine.

**Release mode**

- **FR-043**: System MUST detect during preflight which release mode applies to the repository — observed or executed — MUST ask the developer once when neither can be determined, and MUST record the answer so subsequent runs do not ask again.
- **FR-044**: In observed mode, System MUST watch the repository's own release path after the merge until it reports a terminal outcome, MUST report a release that has not confirmed within the configured wait limit as undetermined rather than as released, and MUST NOT infer a release from the merge.
- **FR-045**: In executed mode, System MUST run the repository's configured release action after the merge, MUST report its outcome, and MUST report a failed release as a failed release with the integration branch left ahead of production, rather than as a failed run with no release attempted.

**Scope boundary**

- **FR-042**: The pipeline capability and the Ship tab capability MUST be separable, such that the pipeline is fully usable with no tab installed and the tab degrades to an honest empty state with no pipeline history. Both are in scope for this feature; the tab is prioritized behind the pipeline (User Stories 4 and 5).

### Key Entities

- **Repository Profile**: What the tool established about a repository — that it is version controlled, its integration branch, its remote, its hosting service, whether automated checks exist, and which release mode applies (observed, executed, or none determinable). Derived by preflight, recorded, and re-verified on later runs.
- **Ship Configuration**: The developer's per-repository choices — source and target branch, pull request composition mode, release definition, wait limits, and repair attempt budget. Persisted with the repository.
- **Ship Run**: One end-to-end attempt to ship a branch. Holds the branch, the pull request it produced, the stage it reached, the outcome of each stage, timings, and whether it is in progress, halted, or complete.
- **Stage Outcome**: The result of a single stage of a run — preflight, commit, publish, pull request, checks, merge, release, cleanup — as succeeded, failed with a classification, or undetermined with a reason.
- **Check Result**: One automated check reported against a pull request, with its name, whether it is required, its outcome, and the location of its output.
- **Release Record**: One confirmed release — what was released, from which merge, at what time, and how the confirmation was obtained.
- **Repair Attempt**: One bounded attempt to fix a classified failure, with the failure it targeted, what was changed, and whether the subsequent checks stage cleared.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer ships a clean feature branch from working copy to released, with cleanup complete, using one command and answering only the confirmation gates — no manual git, hosting-service, or release steps.
- **SC-002**: The tool correctly identifies the integration branch in at least 95% of repositories it is run against on the first attempt, and asks rather than guesses in the remainder.
- **SC-003**: A run started in a directory that is not a repository, or from an unsafe working-copy state, stops before changing anything, in 100% of cases.
- **SC-004**: When a run halts on failure, the developer can name the failing stage and the specific cause from the run's report alone, without opening the hosting service's web interface, in at least 90% of halted runs.
- **SC-005**: A red pipeline caused by a merge conflict with the integration branch is resolved by the run without developer intervention in at least 70% of cases.
- **SC-006**: No run merges a pull request or reports a release without an explicit developer confirmation for that run — measured as zero unconfirmed merges or releases across all runs.
- **SC-007**: No value displayed on the Ship tab is a placeholder, sample, or inferred default; every displayed value traces to recorded state or is labeled undetermined — verified across every panel.
- **SC-008**: An interrupted run resumed by re-issuing the command continues from the stage it reached, creating no duplicate pull request and no duplicate release, in 100% of resumptions.
- **SC-009**: Time from finishing work to a released change drops by at least half compared with performing the same sequence by hand, measured on the same repository.
- **SC-010**: A developer new to a repository can answer "what is committed, what is open, what is deployed, and how fresh is that answer" from the Ship tab within 30 seconds of opening it.
- **SC-011**: The run identifies the correct release mode without asking in at least 80% of repositories it is run against, and asks once rather than guessing in the remainder.
- **SC-012**: No release is reported as complete without a confirmation from the release path itself — measured as zero releases reported from a merge outcome alone, in either release mode.

## Assumptions

- The hosting service is GitHub, since the description names it explicitly; other hosting services are out of scope for this feature, though nothing in the requirements should preclude adding them later.
- "Repo agnostic" means the tool makes no assumptions about a repository's branch names, project language, build tooling, or release mechanism — not that it supports every hosting service.
- The developer already has working, authorized credentials for the hosting service on the machine; obtaining or storing those credentials is out of scope.
- Automated checks are whatever the repository already reports against a pull request; this feature configures no checks of its own.
- Merge and release are confirmed by the developer on every run by default, because both are outward-facing and hard to reverse. An explicit per-run unattended authorization is the way to skip the prompts; there is no persistent always-yes setting.
- The repair budget for a red pipeline is bounded and small by default, because unbounded automatic repair on a shared branch is not safe. The budget is configurable.
- Branch protection rules, required reviews, and required signatures belong to the repository and are respected as given. The tool reports when a rule blocks a stage; it never attempts to bypass one.
- The Ship tab consumes the same per-repository state file the pipeline writes, following the pattern the Companion's existing tabs already use. It reads that file and does not write pipeline state itself.
- The Ship tab is a reporting and configuration surface. Displaying state the tool has not actually established — as a placeholder, a sample, or an inferred default — is a defect, not a presentation choice.
- Both the pipeline and the Ship tab are in scope for this feature, with the tab prioritized behind the pipeline. The tab is kept separable at every seam so it can be packaged as a distinct community extension invoked as `/speckit-community-ship` without rework; the recorded state file is the contract between them, and it is designed once with its consumer in view.
- Release mode is detected per repository and both modes are supported. A repository whose automation releases on merge is handled in observed mode; a repository needing an explicit release action is handled in executed mode. Neither mode infers a release from a merge — a release is reported only when the release path confirms it.
- In executed mode the release action is supplied by the repository's own configuration. The tool runs what the repository declares; it does not compose or infer a release action, and the developer's per-run confirmation gate (FR-013) applies before it runs.
- Only one ship run at a time per repository is supported; concurrent runs are detected and refused rather than serialized.
- A single-target release is assumed. Repositories whose integration branch feeds several independent release targets are recognized and reported as unsupported rather than partially released.
