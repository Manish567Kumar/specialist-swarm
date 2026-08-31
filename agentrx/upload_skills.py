"""
upload_skills.py — packages each skill in skills/ and attaches it to the
matching specialist, via the Skills API. Mirrors ../upload_skills.py exactly;
retargeted from Deal Desk playbooks onto diagnostic playbooks.

The README names this option's landed concept as "Skills, plugins & sub-
agents" — skills aren't optional garnish here, they're one of the three
things this build is meant to demonstrate.
"""
import json
import os
from pathlib import Path

from anthropic import Anthropic
from anthropic.lib import files_from_dir

HERE = Path(__file__).resolve().parent
SPECIALIST_IDS_PATH = HERE / ".specialist_ids.json"
SKILL_IDS_PATH = HERE / ".skill_ids.json"

BETA_HEADER = {"anthropic-beta": "managed-agents-2026-04-01"}

SKILL_TO_SPECIALIST = {
    "prompt-logic-playbook": "prompt_logic",
    "tool-spec-playbook": "tool_spec",
    "context-playbook": "context",
}


def upload_and_attach():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")
    if not SPECIALIST_IDS_PATH.exists():
        raise SystemExit("Run specialists.py first (creates .specialist_ids.json).")
    specialist_ids = json.loads(SPECIALIST_IDS_PATH.read_text())

    client = Anthropic(default_headers=BETA_HEADER)

    print("Checking for existing skills...")
    existing_by_name = {}
    for page in client.beta.skills.list(source="custom"):
        existing_by_name[page.display_name] = page.id

    uploaded = {}
    for skill_name, specialist_key in SKILL_TO_SPECIALIST.items():
        skill_dir = HERE / "skills" / skill_name
        if not (skill_dir / "SKILL.md").exists():
            print(f"  skipping {skill_name} — no SKILL.md found")
            continue

        display_name = skill_name.replace("-", " ").title()

        if display_name in existing_by_name:
            skill_id = existing_by_name[display_name]
            print(f"  reusing existing skill: {skill_name} ({skill_id})")
        else:
            print(f"  uploading skill: {skill_name}...")
            skill = client.beta.skills.create(
                display_name=display_name,
                files=files_from_dir(str(skill_dir)),
            )
            skill_id = skill.id
            print(f"    -> {skill_id}")
        uploaded[skill_name] = skill_id

        specialist_id = specialist_ids[specialist_key]
        print(f"  attaching to specialist `{specialist_key}` ({specialist_id})...")
        current = client.beta.agents.retrieve(specialist_id)
        existing_skills = current.skills or []
        already_attached = any(getattr(s, "skill_id", None) == skill_id for s in existing_skills)
        if already_attached:
            print("    already attached")
            continue
        new_skills = [s.model_dump() for s in existing_skills] + [
            {"type": "custom", "skill_id": skill_id, "version": "latest"}
        ]
        client.beta.agents.update(specialist_id, version=current.version, skills=new_skills)
        print("    attached")

    SKILL_IDS_PATH.write_text(json.dumps(uploaded, indent=2))
    print(f"\nuploaded/attached {len(uploaded)} skills")
    return uploaded


if __name__ == "__main__":
    upload_and_attach()
