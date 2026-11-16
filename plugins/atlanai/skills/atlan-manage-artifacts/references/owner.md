# Changing an artifact's owner

The owner answers for an artifact and can release it. `owner_id` takes a person's `user_…` id. A
change moves it on every version, makes no new version and needs no release.

## 1. Find the person

`registry__list_users`, a read, with `query` set to the name or email the user gave.

| Answer | Do |
|---|---|
| One row | That is the person. |
| Several rows | Name each with their email and ask which one. |
| No rows | Say no one in the account matches what they gave, and ask for the person's email. If the email finds no one either, they have not signed in to Atlan yet or have left the account: a person signs in once before they can own anything. Stop there. |
| `insufficient_scope` | This connection cannot read the account's people. Ask for the person's user id, never guess one; a metric or saved query can also change owner from its page in Metric Studio. |

When `mcp__describe_tools` answers that `registry__list_users` is not a tool you can call (older
gateways do not serve it), ask for the user id the same way. That gateway's
`registry__patch_artifact` may take no `if_match` either: step 4 says how to write there.

## 2. Read who may change it

Send together: `registry__get_artifact` on the head, keeping its `etag`, `owner_id` and
`workspace_id`; `registry__list_users` with `query` set to that `owner_id`, to name the owner
now; and `registry__list_workspaces`, whose row for that workspace says `can_manage` when the
user is its admin. Where `registry__list_users` is not served or is refused, leave it out and
name the owner by their id.

Its owner or an admin of its workspace may change it. No tool says who the user is, so when
`can_manage` is false:

- **It has no owner.** Only an admin of its workspace can give it one. Say who can and stop:
  nothing is written.
- **It has an owner.** Only that person or an admin can change it, and you cannot tell whether
  the user is that person. Say so in place of step 3's question, and write only when they answer
  that they are the owner. Any other answer stops it: nothing is written.

> terraform-plan-review in Platform Engineering. Only Priya Nair or a Platform Engineering admin
> can change its owner. If you are Priya Nair, say so and it goes to Stefan Novak
> (stefan@acme.example), who can then release it.

## 3. Ask once

Name the artifact and its workspace, the owner now and the new one with their email, and that the
new owner can release it. For example:

> terraform-plan-review in Platform Engineering. Owner: Priya Nair, to become Stefan Novak
> (stefan@acme.example), who can then release it. It applies to every version now. Change it?

## 4. Write, then read back

`registry__patch_artifact` with `artifact_id`, `patch: {"owner_id": "<user_id>"}` and `if_match`
set to the `etag` read in step 2. A gateway that serves `registry__list_users` refuses an owner
change without `if_match`, so never drop it to get past a refusal.

On an older gateway, one without `registry__list_users`, send `if_match` only when
`mcp__describe_tools` lists it for `registry__patch_artifact`; one that does not list it refuses
it as an unknown argument. Without it nothing fences the write, so read the head again just
before it: when its `etag` or `owner_id` is not what the readout showed, show the new readout
instead of writing.

Read the artifact back and report the new owner in one line.

| Refusal | Do |
|---|---|
| `precondition_failed` | It changed since you read it. Read it again and show the new readout; never resend. |
| `invalid_arguments` naming `if_match` | The gateway wants the other form of step 4: send the `etag` read in step 2, or, on a gateway that does not list `if_match`, read the head again and write without it. Never repeat the same call. |
| `forbidden` | Only its owner or an admin of its workspace can change it, and only an admin when it has none. Say who, and stop. |
| `unprocessable` | That person is not a user in the account: they have not signed in to Atlan yet or have left it. |
