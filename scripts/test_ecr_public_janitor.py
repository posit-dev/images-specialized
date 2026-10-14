import json
import subprocess
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from unittest import mock

_loader = SourceFileLoader("janitor", str(Path(__file__).parent / "ecr-public-janitor"))
janitor = module_from_spec(spec_from_loader("janitor", _loader))
_loader.exec_module(janitor)

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
OLD = "2024-01-01T00:00:00+00:00"  # > 548 days
MID = "2025-06-01T00:00:00.123000+00:00"  # young tagged, > 30 days
NEW = "2026-05-25T00:00:00Z"
INDEX = "application/vnd.oci.image.index.v1+json"
IMAGE = "application/vnd.oci.image.manifest.v1+json"


def img(digest, pushed, tags=None, media=IMAGE):
    d = {"imageDigest": digest, "imagePushedAt": pushed, "imageManifestMediaType": media}
    if tags:
        d["imageTags"] = tags
    return d


def index_manifest(image, soci):
    return {
        "annotations": {"com.amazon.soci.something-digest": "ignored-if-not-sha"},
        "manifests": [
            {"digest": image, "annotations": {"com.amazon.soci.index-digest": soci}},
            {"digest": soci, "artifactType": "application/vnd.amazon.soci.index.v2+json",
             "annotations": {"com.amazon.soci.image-manifest-digest": image}},
        ],
    }


def tagged_family(prefix, pushed, tags):
    """Tagged index + image + SOCI children; returns (images, manifests)."""
    idx, a, s = f"sha256:{prefix}-idx", f"sha256:{prefix}-img", f"sha256:{prefix}-soci"
    return (
        [img(idx, pushed, tags, INDEX), img(a, pushed), img(s, pushed)],
        {idx: index_manifest(a, s)},
    )


def plan(images, manifests, **kw):
    layers, reasons = janitor.plan_repo(images, lambda d: manifests.get(d, {}), NOW, **kw)
    return [d for layer in layers for d in layer], reasons


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.keep_images, self.manifests = tagged_family("keep", MID, ["1.0"])

    def test_children_of_kept_tag_survive_even_when_old(self):
        images, manifests = tagged_family("keep", MID, ["1.0"])
        # children pushed long ago, still referenced
        images[1]["imagePushedAt"] = images[2]["imagePushedAt"] = OLD
        order, _ = plan(images, manifests)
        self.assertEqual(order, [])

    def test_orphan_old_child_deleted_recent_untagged_kept(self):
        images = self.keep_images + [img("sha256:orphan", OLD), img("sha256:fresh", NEW)]
        order, _ = plan(images, self.manifests)
        self.assertEqual(order, ["sha256:orphan"])

    def test_expired_tag_deleted_with_children_parents_first(self):
        old_images, old_manifests = tagged_family("old", OLD, ["0.1"])
        order, _ = plan(self.keep_images + old_images, {**self.manifests, **old_manifests})
        self.assertEqual(order[0], "sha256:old-idx")
        self.assertEqual(set(order), {"sha256:old-idx", "sha256:old-img", "sha256:old-soci"})

    def test_protected_tags_kept_when_old(self):
        for tag in ("latest", "std", "min"):
            old_images, old_manifests = tagged_family(tag, OLD, [tag, "9.9"])
            order, _ = plan(self.keep_images + old_images, {**self.manifests, **old_manifests})
            self.assertEqual(order, [], tag)

    def test_refuses_to_delete_every_tagged_image(self):
        old_images, old_manifests = tagged_family("old", OLD, ["0.1"])
        with self.assertRaises(SystemExit):
            plan(old_images, old_manifests)

    def test_untagged_referrer_of_kept_image_kept(self):
        images = self.keep_images + [img("sha256:sbom", OLD)]
        manifests = {**self.manifests, "sha256:sbom": {"subject": {"digest": "sha256:keep-idx"}}}
        self.assertEqual(plan(images, manifests)[0], [])

    def test_nested_index_children_kept(self):
        images = [img("sha256:top", MID, ["1.0"], INDEX), img("sha256:mid", OLD, media=INDEX), img("sha256:leaf", OLD)]
        manifests = {"sha256:top": {"manifests": [{"digest": "sha256:mid"}]},
                     "sha256:mid": {"manifests": [{"digest": "sha256:leaf"}]}}
        self.assertEqual(plan(images, manifests)[0], [])

    def test_referrer_index_child_kept(self):
        images = self.keep_images + [img("sha256:ref", OLD, media=INDEX), img("sha256:refchild", OLD)]
        manifests = {**self.manifests, "sha256:ref": {"subject": {"digest": "sha256:keep-idx"},
                                                       "manifests": [{"digest": "sha256:refchild"}]}}
        self.assertEqual(plan(images, manifests)[0], [])

    def test_referrer_soci_annotation_kept(self):
        images = self.keep_images + [img("sha256:ref", OLD), img("sha256:sociblob", OLD)]
        manifests = {**self.manifests, "sha256:ref": {
            "subject": {"digest": "sha256:keep-idx"},
            "annotations": {"com.amazon.soci.index-digest": "sha256:sociblob"}}}
        self.assertEqual(plan(images, manifests)[0], [])

    def test_tagged_child_of_protected_index_kept(self):
        images = [img("sha256:latest", OLD, ["latest"], INDEX), img("sha256:old01", OLD, ["0.1"]),
                  img("sha256:other", OLD, ["0.2"])]
        manifests = {"sha256:latest": {"manifests": [{"digest": "sha256:old01"}]}}
        self.assertEqual(plan(images, manifests)[0], ["sha256:other"])

    def test_nested_index_deleted_outer_before_inner_before_leaf(self):
        images = self.keep_images + [img("sha256:leaf", OLD), img("sha256:inner", OLD, media=INDEX),
                                     img("sha256:outer", OLD, media=INDEX)]
        manifests = {**self.manifests, "sha256:outer": {"manifests": [{"digest": "sha256:inner"}]},
                     "sha256:inner": {"manifests": [{"digest": "sha256:leaf"}]}}
        layers, _ = janitor.plan_repo(images, lambda d: manifests.get(d, {}), NOW)
        self.assertEqual(layers, [["sha256:outer"], ["sha256:inner"], ["sha256:leaf"]])

    def test_referrer_deleted_before_its_subject(self):
        images = self.keep_images + [img("sha256:subj", OLD), img("sha256:ref", OLD)]
        manifests = {**self.manifests, "sha256:ref": {"subject": {"digest": "sha256:subj"}}}
        layers, _ = janitor.plan_repo(images, lambda d: manifests.get(d, {}), NOW)
        self.assertEqual(layers, [["sha256:ref"], ["sha256:subj"]])

    def test_custom_thresholds_and_protected_tags(self):
        images = self.keep_images + [img("sha256:orphan", MID), img("sha256:t", MID, ["x"])]
        order, _ = plan(images, self.manifests, tagged_days=1, untagged_days=1, protected={"x"})
        self.assertIn("sha256:orphan", order)
        self.assertNotIn("sha256:t", order)


class DeleteTest(unittest.TestCase):
    def test_chunks_of_100(self):
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            return json.dumps({"imageIds": [{"imageDigest": a[len("imageDigest="):]}
                                            for a in cmd if a.startswith("imageDigest=")]})

        layers = [[f"sha256:{n}" for n in range(250)]]
        with mock.patch.object(janitor, "run", fake_run):
            deleted, failures = janitor.delete_digests("repo", layers)
        self.assertEqual([sum(a.startswith("imageDigest=") for a in c) for c in calls], [100, 100, 50])
        self.assertEqual((len(deleted), failures), (250, []))

    def test_stops_after_response_failure(self):
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            return json.dumps({"failures": [{"imageId": {"imageDigest": "sha256:0"}, "failureReason": "nope"}]})

        with mock.patch.object(janitor, "run", fake_run):
            deleted, failures = janitor.delete_digests("repo", [["sha256:0"], ["sha256:1"]])
        self.assertEqual((len(calls), deleted, failures), (1, [], ["sha256:0: nope"]))

    def test_stops_after_command_failure(self):
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            raise subprocess.CalledProcessError(1, cmd, stderr="boom")

        with mock.patch.object(janitor, "run", fake_run):
            deleted, failures = janitor.delete_digests("repo", [["sha256:0"], ["sha256:1"]])
        self.assertEqual((len(calls), deleted, failures), (1, [], ["sha256:0: boom"]))

    def test_main_dry_run_does_not_delete(self):
        images = [img("sha256:keep", MID, ["1.0"]), img("sha256:orphan", OLD)]

        def fake_run(cmd):
            self.assertNotIn("batch-delete-image", cmd)
            if cmd[0] == "aws":
                return json.dumps({"imageDetails": images})
            return "{}"

        with mock.patch.object(janitor, "run", fake_run), mock.patch("builtins.print"):
            self.assertEqual(janitor.main(["repo", "--dry-run"]), 0)


class FailClosedTest(unittest.TestCase):
    """latest -> mid -> leaf, leaf expired; any gap in knowledge of mid must abort before deletion."""

    def run_main(self, images, fetch_manifest):
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            if cmd[0] == "aws" and "describe-images" in cmd:
                return json.dumps({"imageDetails": images})
            if cmd[0] == "oras":
                return json.dumps(fetch_manifest(cmd[-1].split("@")[1]))
            return json.dumps({"imageIds": []})

        with mock.patch.object(janitor, "run", fake_run), mock.patch("builtins.print"):
            try:
                return calls, None, janitor.main(["repo"])
            except (SystemExit, subprocess.CalledProcessError) as e:
                return calls, e, None

    def assert_no_delete(self, calls, error):
        self.assertIsNotNone(error)
        self.assertFalse([c for c in calls if "batch-delete-image" in c])

    def test_intermediate_missing_from_inventory_aborts(self):
        images = [img("sha256:latest", MID, ["latest"], INDEX), img("sha256:leaf", OLD)]
        chain = {"sha256:latest": {"manifests": [{"digest": "sha256:mid"}]}}
        calls, error, _ = self.run_main(images, lambda d: chain.get(d, {}))
        self.assert_no_delete(calls, error)

    def test_intermediate_fetch_failure_aborts(self):
        images = [img("sha256:latest", MID, ["latest"], INDEX), img("sha256:mid", OLD, media=INDEX),
                  img("sha256:leaf", OLD)]
        chain = {"sha256:latest": {"manifests": [{"digest": "sha256:mid"}]}}

        def fetch(d):
            if d == "sha256:mid":
                raise subprocess.CalledProcessError(1, "oras", stderr="boom")
            return chain.get(d, {})

        calls, error, _ = self.run_main(images, fetch)
        self.assert_no_delete(calls, error)


if __name__ == "__main__":
    unittest.main()
