# Changing an artifact's owner

The owner answers for an artifact and can release it. `owner_id` takes a person's `user_…` id. A
change moves it on every version, makes no new version and needs no release.

## 1. Find the person

`registry__list_users`, a read, with `query` set to the name or email the user gave.

| Answer | Do |
|---|---|
| One row | That is the person. |
| Several rows | Name each with their email and ask which one. |
| No rows | Say they have not signed in to Atlan yet: a person signs in once before they can own anything. Stop. |
| `insufficient_scope` | This connection cannot read the account's people. Ask for the person's user id, never guess one; a metric or saved query can also change owner from its page in Metric Studio. |

When `mcp__describe_tools` answers that `registry__list_users` is not a tool you can call (older
gateways do not serve it), ask for the user id the same way.

## 2. Read who may change it

Send together: `registry__get_artifact` on the head, keeping its `etag`, `owner_id` and
`workspace_id`; `registry__list_users` with `query` set to that `owner_id`, to name the owner
now; and `registry__list_workspaces`, whose row for that workspace says `can_manage` when the
user is its admin.

An artifact with no owner takes one only from an admin of its workspace. When the user is not
one, say who can and stop: nothing is written.

## 3. Ask once

Name the artifact and its workspace, the owner now and the new one with their email, and that the
new owner can release it. For example:

> terraform-plan-review in Platform Engineering. Owner: Priya Nair, to become Stefan Novak
> (stefan@acme.example), who can then release it. It applies to every version now. Change it?

## 4. Write, then read back

`registry__patch_artifact` with `artifact_id`, `patch: {"owner_id": "<user_id>"}` and `if_match`
set to the `etag` read in step 2. Read the artifact back and report the new owner in one line.

| Refusal | Do |
|---|---|
| `precondition_failed` | It changed since you read it. Read it again and show the new readout; never resend. |
| `forbidden` | Only its owner or an admin of its workspace can change it, and only an admin when it has none. Say who, and stop. |
| `unprocessable` | That person is not a user in the account yet. |
