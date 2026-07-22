# AI Agent Provisioning Collection

Ansible collection `andreasbehnke.ai_agent`, holding the roles which provision AI agents
(agent runtimes, their chat tools and dependencies) on Debian/Ubuntu machines.

## Folder Structure

* galaxy.yml - collection metadata, bump `version` when the collection changes
* meta/runtime.yml - minimum supported ansible version
* roles - the roles of this collection, one directory per role, standard role layout

Role directories must be named lowercase with underscores; a role is referenced as
`andreasbehnke.ai_agent.<role_name>`.

## Consumption

The infrastructure repository `platform` lists this repository in `ansible/collections.txt`
and clones it into `ansible/collections/ansible_collections/andreasbehnke/ai_agent` via
`ansible/setup.sh`. Changes must be pushed to the default branch before `setup.sh` picks
them up.
