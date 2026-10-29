"""核心模型测试：目标树、撤销栈、导入语义、冲突、命名模板。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from zipmerge.core import (
    ConflictKind,
    Mapping,
    MergeSession,
    NamingFields,
    NodeKind,
    ResolveStrategy,
    TargetNode,
    UndoStack,
    build_export_plan,
    detect_conflicts,
    render_filename,
    resolve_mappings,
    suggest_content_tag,
    unique_output_path,
)
from zipmerge.core.commands import AddNodes, MoveNodes, RemoveNodes, RenameNode, ReplaceNode
from zipmerge.core.imports import (
    archive_display_name,
    build_disk_nodes,
    build_entry_subtree,
    flatten_levels,
)
from zipmerge.core.nodes import make_virtual_dir, new_root, unique_child_name, validate_name
from zipmerge.core.template import bump_version
from zipmerge.engine import ArchiveEntry


def tree_snapshot(root: TargetNode) -> list[str]:
    return [f"{node.path()}{'/' if node.is_dir else ''}" for node in root.walk(include_self=False)]


def file_node(name: str) -> TargetNode:
    return TargetNode(name=name, is_dir=False, kind=NodeKind.VIRTUAL_DIR)


class TargetNodeTests(unittest.TestCase):
    def test_path_and_walk(self) -> None:
        root = new_root()
        folder = make_virtual_dir("子目录")
        deep = make_virtual_dir("更深")
        leaf = file_node("a.txt")
        root.add(folder)
        folder.add(deep)
        deep.add(leaf)

        self.assertEqual(leaf.path(), "子目录\\更深\\a.txt")
        self.assertEqual(folder.parent, root)
        self.assertEqual(root.total_file_count(), 1)
        self.assertTrue(root.is_ancestor_of(leaf))
        self.assertFalse(leaf.is_ancestor_of(root))

    def test_clone_is_deep_and_identity_distinct(self) -> None:
        root = new_root()
        folder = make_virtual_dir("a")
        folder.add(file_node("b.txt"))
        root.add(folder)

        copy = folder.clone()
        self.assertEqual(copy.path() if copy.parent else "a", "a")
        self.assertIsNot(copy, folder)
        self.assertIsNot(copy.children[0], folder.children[0])
        self.assertNotEqual(copy.uid, folder.uid)
        copy.name = "改名"
        self.assertEqual(folder.name, "a")

    def test_validate_name(self) -> None:
        self.assertIsNone(validate_name("正常名称.txt"))
        self.assertIsNotNone(validate_name(""))
        self.assertIsNotNone(validate_name("   "))
        self.assertIsNotNone(validate_name("a/b"))
        self.assertIsNotNone(validate_name("a:b"))
        self.assertIsNotNone(validate_name(".."))
        self.assertIsNotNone(validate_name("tail."))
        self.assertIn("/", validate_name("a/b") or "")

    def test_unique_child_name(self) -> None:
        root = new_root()
        root.add(make_virtual_dir("新建文件夹"))
        root.add(make_virtual_dir("新建文件夹2"))
        self.assertEqual(unique_child_name(root, "新建文件夹"), "新建文件夹3")
        self.assertEqual(unique_child_name(root, "素材"), "素材")


class TargetPlacementConflictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zipmerge-placement-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.session = MergeSession(name="同名处理", temp_root=self.tmp / "temp")
        self.addCleanup(self.session.close)

    def test_batch_rename_reports_once_and_uses_case_insensitive_names(self) -> None:
        old_a = file_node("a.wav")
        old_b = file_node("B.wav")
        self.session.root.extend([old_a, old_b])
        incoming = [file_node("A.wav"), file_node("b.wav")]
        seen: list[int] = []

        placed = self.session.add_nodes(
            self.session.root,
            incoming,
            conflict_resolver=lambda count: seen.append(count) or ResolveStrategy.RENAME,
        )

        self.assertEqual(seen, [2])
        self.assertEqual([node.name for node in placed], ["A_1.wav", "b_1.wav"])
        self.assertEqual(
            [node.name for node in self.session.root.children],
            ["a.wav", "B.wav", "A_1.wav", "b_1.wav"],
        )

    def test_overwrite_uses_incoming_file_and_undo_restores_old_file(self) -> None:
        old = file_node("take.wav")
        incoming = file_node("TAKE.wav")
        self.session.root.add(old)

        placed = self.session.add_nodes(
            self.session.root,
            [incoming],
            conflict_resolver=lambda _count: ResolveStrategy.OVERWRITE,
        )

        self.assertEqual(placed, [incoming])
        self.assertEqual(self.session.root.children, [incoming])
        self.session.undo.undo()
        self.assertEqual(self.session.root.children, [old])
        self.session.undo.redo()
        self.assertEqual(self.session.root.children, [incoming])

    def test_cancel_keeps_target_tree_unchanged(self) -> None:
        old = file_node("same.txt")
        incoming = file_node("same.txt")
        self.session.root.add(old)

        placed = self.session.add_nodes(
            self.session.root,
            [incoming],
            conflict_resolver=lambda _count: None,
        )

        self.assertEqual(placed, [])
        self.assertEqual(self.session.root.children, [old])
        self.assertFalse(self.session.undo.can_undo)

    def test_rename_collision_can_overwrite_and_undo_as_one_operation(self) -> None:
        old = file_node("old.txt")
        incoming = file_node("new.txt")
        self.session.root.extend([old, incoming])

        changed = self.session.rename(
            incoming,
            "OLD.txt",
            conflict_resolver=lambda count: ResolveStrategy.OVERWRITE if count == 1 else None,
        )

        self.assertTrue(changed)
        self.assertEqual(self.session.root.children, [incoming])
        self.assertEqual(incoming.name, "OLD.txt")
        self.session.undo.undo()
        self.assertEqual(self.session.root.children, [old, incoming])
        self.assertEqual(incoming.name, "new.txt")

    def test_move_overwrite_is_reversible(self) -> None:
        source = make_virtual_dir("source")
        target = make_virtual_dir("target")
        incoming = file_node("same.wav")
        old = file_node("SAME.wav")
        source.add(incoming)
        target.add(old)
        self.session.root.extend([source, target])

        changed = self.session.move(
            [incoming],
            target,
            len(target.children),
            conflict_resolver=lambda _count: ResolveStrategy.OVERWRITE,
        )

        self.assertTrue(changed)
        self.assertEqual(source.children, [])
        self.assertEqual(target.children, [incoming])
        self.session.undo.undo()
        self.assertEqual(source.children, [incoming])
        self.assertEqual(target.children, [old])

    def test_flatten_rename_keeps_both_files_and_undo_restores_wrapper(self) -> None:
        old = file_node("same.wav")
        wrapper = make_virtual_dir("wrapper")
        incoming = file_node("same.wav")
        wrapper.add(incoming)
        self.session.root.extend([old, wrapper])

        count = self.session.flatten(
            wrapper,
            0,
            conflict_resolver=lambda _count: ResolveStrategy.RENAME,
        )

        self.assertEqual(count, 1)
        self.assertEqual([node.name for node in self.session.root.children], ["same.wav", "same_1.wav"])
        self.session.undo.undo()
        self.assertEqual(self.session.root.children, [old, wrapper])
        self.assertEqual(wrapper.children, [incoming])
        self.assertEqual(incoming.name, "same.wav")


class UndoStackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = new_root()

    def test_add_undo_redo(self) -> None:
        stack = UndoStack(limit=10)
        node = make_virtual_dir("a")
        stack.do(AddNodes(parent=self.root, nodes=[node]))
        self.assertEqual(len(self.root.children), 1)
        stack.undo()
        self.assertEqual(len(self.root.children), 0)
        self.assertIsNone(node.parent)
        stack.redo()
        self.assertEqual(len(self.root.children), 1)
        self.assertIs(node.parent, self.root)

    def test_remove_restores_index(self) -> None:
        stack = UndoStack(limit=10)
        nodes = [make_virtual_dir(name) for name in ("a", "b", "c")]
        self.root.extend(nodes)
        stack.do(RemoveNodes(nodes=[nodes[1]]))
        self.assertEqual([n.name for n in self.root.children], ["a", "c"])
        stack.undo()
        self.assertEqual([n.name for n in self.root.children], ["a", "b", "c"])

    def test_rename(self) -> None:
        stack = UndoStack(limit=10)
        node = make_virtual_dir("旧")
        self.root.add(node)
        stack.do(RenameNode(node=node, new_name="新"))
        self.assertEqual(node.name, "新")
        stack.undo()
        self.assertEqual(node.name, "旧")

    def test_move_across_parents_and_restore(self) -> None:
        stack = UndoStack(limit=10)
        left = make_virtual_dir("left")
        right = make_virtual_dir("right")
        self.root.extend([left, right])
        a, b, c = (make_virtual_dir(name) for name in ("a", "b", "c"))
        left.extend([a, b, c])

        stack.do(MoveNodes(moves=[(b, left, 1)], target=right, index=0))
        self.assertEqual([n.name for n in left.children], ["a", "c"])
        self.assertEqual([n.name for n in right.children], ["b"])
        stack.undo()
        self.assertEqual([n.name for n in left.children], ["a", "b", "c"])
        self.assertEqual([n.name for n in right.children], [])

    def test_multiple_moves_restore_order(self) -> None:
        stack = UndoStack(limit=10)
        source = make_virtual_dir("src")
        dest = make_virtual_dir("dst")
        self.root.extend([source, dest])
        items = [make_virtual_dir(name) for name in ("a", "b", "c", "d")]
        source.extend(items)

        moves = [(items[0], source, 0), (items[2], source, 2)]
        stack.do(MoveNodes(moves=moves, target=dest, index=0))
        self.assertEqual([n.name for n in source.children], ["b", "d"])
        self.assertEqual([n.name for n in dest.children], ["a", "c"])
        stack.undo()
        self.assertEqual([n.name for n in source.children], ["a", "b", "c", "d"])
        self.assertEqual([n.name for n in dest.children], [])

    def test_replace_node_flatten_round_trip(self) -> None:
        stack = UndoStack(limit=10)
        wrapper = make_virtual_dir("包名")
        inner = make_virtual_dir("素材")
        leaf = file_node("a.txt")
        inner.add(leaf)
        wrapper.add(inner)
        self.root.add(wrapper)

        replacements = flatten_levels(wrapper, 0)
        stack.do(ReplaceNode(node=wrapper, replacements=replacements))
        self.assertEqual([n.name for n in self.root.children], ["素材"])
        self.assertEqual(leaf.path(), "素材\\a.txt")
        stack.undo()
        self.assertEqual([n.name for n in self.root.children], ["包名"])
        self.assertEqual(leaf.path(), "包名\\素材\\a.txt")

    def test_limit_drops_oldest(self) -> None:
        stack = UndoStack(limit=2)
        for name in ("a", "b", "c"):
            stack.do(AddNodes(parent=self.root, nodes=[make_virtual_dir(name)]))
        self.assertEqual(len(self.root.children), 3)
        stack.undo()
        stack.undo()
        self.assertFalse(stack.can_undo)
        self.assertEqual(len(self.root.children), 1)


class ImportTests(unittest.TestCase):
    def test_archive_display_name(self) -> None:
        self.assertEqual(archive_display_name("项目xxx_SFX_20260910_Demo_V1.zip"), "项目xxx_SFX_20260910_Demo_V1")
        self.assertEqual(archive_display_name("0910项目xxx_vo_demo.7z"), "0910项目xxx_vo_demo")
        self.assertEqual(archive_display_name("pkg.tar.gz"), "pkg")

    def test_build_entry_subtree_synthesizes_missing_dirs(self) -> None:
        entries = [
            ArchiveEntry(path="a\\b\\c.txt", size=3),
            ArchiveEntry(path="空目录", is_dir=True),
        ]
        nodes = build_entry_subtree(entries, source_id="s1", archive_path="x.zip")
        root = new_root()
        root.extend(nodes)
        self.assertEqual(
            tree_snapshot(root),
            ["空目录/", "a/", "a\\b/", "a\\b\\c.txt"],
        )
        by_name = {node.name: node for node in root.walk()}
        leaf = by_name["c.txt"]
        self.assertEqual(leaf.source.entry_path, "a\\b\\c.txt")
        self.assertEqual(leaf.source.source_id, "s1")
        # 合成的中间目录保留包内完整路径，供提取时使用
        self.assertEqual(by_name["b"].source.entry_path, "a\\b")

    def test_build_entry_subtree_with_prefix(self) -> None:
        entries = [
            ArchiveEntry(path="包名\\SFX\\a.wav", size=1),
            ArchiveEntry(path="包名\\SFX\\b.wav", size=1),
            ArchiveEntry(path="包名\\readme.txt", size=1),
        ]
        nodes = build_entry_subtree(entries, source_id="s1", archive_path="x.zip", prefix="包名\\SFX")
        root = new_root()
        root.extend(nodes)
        self.assertEqual(tree_snapshot(root), ["a.wav", "b.wav"])

    def test_flatten_levels(self) -> None:
        wrapper = make_virtual_dir("包名")
        sfx = make_virtual_dir("SFX")
        deep = make_virtual_dir("更深")
        sfx.add(file_node("a.wav"))
        sfx.add(deep)
        deep.add(file_node("b.wav"))
        wrapper.add(sfx)

        level0 = flatten_levels(wrapper, 0)
        self.assertEqual([n.name for n in level0], ["SFX"])
        level1 = flatten_levels(wrapper, 1)
        self.assertEqual([n.name for n in level1], ["a.wav", "更深"])
        full = flatten_levels(wrapper, None)
        self.assertEqual([n.name for n in full], ["a.wav", "b.wav"])

    def test_build_disk_nodes(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="zipmerge-disk-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        folder = tmp / "素材"
        folder.mkdir()
        (folder / "a.txt").write_text("x", encoding="utf-8")
        loose = tmp / "说明.txt"
        loose.write_text("y", encoding="utf-8")

        result = build_disk_nodes([folder, loose, tmp / "缺失.txt"])
        self.assertEqual([node.name for node in result.nodes], ["素材", "说明.txt"])
        self.assertIs(result.nodes[0].kind, NodeKind.DISK_DIR)
        self.assertIs(result.nodes[1].kind, NodeKind.DISK_FILE)
        self.assertEqual(len(result.skipped), 1)


class ConflictTests(unittest.TestCase):
    def _mappings(self) -> list[Mapping]:
        sfx = make_virtual_dir("SFX")
        first = TargetNode(name="a.wav", is_dir=False, kind=NodeKind.ARCHIVE_ENTRY)
        second = TargetNode(name="a.wav", is_dir=False, kind=NodeKind.ARCHIVE_ENTRY)
        sfx.add(first)
        sfx.add(second)
        root = new_root()
        root.add(sfx)
        return build_export_plan(root)

    def test_detect_duplicates(self) -> None:
        conflicts = detect_conflicts(self._mappings())
        self.assertEqual(len(conflicts), 1)
        self.assertIs(conflicts[0].kind, ConflictKind.DUPLICATE)
        self.assertEqual(conflicts[0].target_path, "SFX\\a.wav")
        self.assertIsNotNone(conflicts[0].first)

    def test_rename_strategy(self) -> None:
        result = resolve_mappings(self._mappings(), ResolveStrategy.RENAME)
        paths = [item.target_path for item in result.mappings if not item.is_dir]
        self.assertEqual(paths, ["SFX\\a.wav", "SFX\\a_1.wav"])
        self.assertEqual(result.renamed, 1)

    def test_skip_strategy(self) -> None:
        result = resolve_mappings(self._mappings(), ResolveStrategy.SKIP)
        paths = [item.target_path for item in result.mappings if not item.is_dir]
        self.assertEqual(paths, ["SFX\\a.wav"])
        self.assertEqual(result.skipped, 1)

    def test_overwrite_strategy(self) -> None:
        mappings = self._mappings()
        result = resolve_mappings(mappings, ResolveStrategy.OVERWRITE)
        files = [item for item in result.mappings if not item.is_dir]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].node.name, "a.wav")
        self.assertEqual(result.overwritten, 1)
        # 覆盖的是后到者
        self.assertIs(files[0].node, mappings[2].node)

    def test_per_item_override(self) -> None:
        result = resolve_mappings(
            self._mappings(), ResolveStrategy.RENAME, {"SFX\\a.wav": ResolveStrategy.SKIP}
        )
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.renamed, 0)

    def test_dir_and_file_same_path_is_conflict(self) -> None:
        root = new_root()
        folder = make_virtual_dir("数据")
        folder.add(file_node("inner.txt"))
        clash = file_node("数据")
        root.add(folder)
        root.add(clash)

        conflicts = detect_conflicts(build_export_plan(root))
        self.assertEqual(len(conflicts), 1)
        self.assertIs(conflicts[0].kind, ConflictKind.PATH_CLASH)

        result = resolve_mappings(build_export_plan(root), ResolveStrategy.RENAME)
        file_paths = [item.target_path for item in result.mappings if not item.is_dir]
        self.assertEqual(sorted(file_paths), ["数据\\inner.txt", "数据_1"])

    def test_same_dir_name_merges_without_conflict(self) -> None:
        root = new_root()
        for holder in ("a", "b"):
            container = make_virtual_dir(holder)
            shared = make_virtual_dir("素材")
            distinct = file_node(f"{holder}.txt")
            shared.add(distinct)
            container.add(shared)
            root.add(container)
        conflicts = detect_conflicts(build_export_plan(root))
        self.assertEqual(conflicts, [])


class TemplateTests(unittest.TestCase):
    def test_default_render(self) -> None:
        fields = NamingFields(
            project="项目xxx", requirement="需求名", content="SFX&VO", date="20260910", version="V1"
        )
        self.assertEqual(
            render_filename("", fields), "项目xxx_需求名_SFX&VO_20260910_V1"
        )

    def test_illegal_chars_replaced(self) -> None:
        fields = NamingFields(project='a/b:c*d?e"f<g>h|i', requirement="", content="", date="", version="")
        rendered = render_filename("{项目名}", fields)
        self.assertEqual(rendered, "a_b_c_d_e_f_g_h_i")

    def test_empty_fields_collapse_separators(self) -> None:
        fields = NamingFields(project="项目", date="20260910", version="")
        rendered = render_filename("", fields)
        self.assertEqual(rendered, "项目_20260910")

    def test_unknown_placeholder_kept(self) -> None:
        rendered = render_filename("{项目名}_{未知}", NamingFields(project="P"))
        self.assertEqual(rendered, "P_{未知}")

    def test_suggest_content_tag(self) -> None:
        self.assertEqual(suggest_content_tag(["项目xxx_SFX_20260910_Demo_V1.zip"]), "SFX")
        self.assertEqual(
            suggest_content_tag(["项目xxx_SFX_20260910_Demo_V1.zip", "0910项目xxx_vo_demo.7z"]),
            "SFX&VO",
        )
        self.assertEqual(suggest_content_tag(["素材包.zip"]), "")
        # 短关键词必须按词边界匹配，避免误命中
        self.assertEqual(suggest_content_tag(["invoice_2026.zip"]), "")

    def test_bump_version(self) -> None:
        self.assertEqual(bump_version("V1"), "V2")
        self.assertEqual(bump_version("v9"), "v10")
        self.assertEqual(bump_version(""), "V2")

    def test_unique_output_path(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="zipmerge-out-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "项目_需求_V1.zip").write_text("x", encoding="utf-8")
        path, bumped = unique_output_path(tmp, "项目_需求_V1", ".zip")
        self.assertTrue(bumped)
        self.assertEqual(path.name, "项目_需求_V2.zip")

        path2, bumped2 = unique_output_path(tmp, "未占用", ".zip")
        self.assertFalse(bumped2)
        self.assertEqual(path2.name, "未占用.zip")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
