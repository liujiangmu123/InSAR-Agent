import './_env.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  UNGROUPED, asProjectId, buildProjectTree, inferCurrentProject, isOpen,
} from '../../prototype/js/projecttree.js';

test('buildProjectTree:会话挂在对应项目下', () => {
  const tree = buildProjectTree(
    [
      { id: 's1', name: '对话甲', project_id: 'p1' },
      { id: 's2', name: '对话乙', project_id: 'p1' },
      { id: 's3', name: '散会话', project_id: null },
      { id: 's4', name: '已归档', project_id: 'p1', archived: 1 },
    ],
    [
      { project_id: 'p1', name: '玉树滑坡', root: 'E:/data/yushu' },
      { project_id: 'p2', name: '空项目', root: 'E:/data/empty' },
    ],
  );
  assert.equal(tree.length, 3);
  assert.equal(tree[0].name, '玉树滑坡');
  assert.deepEqual(tree[0].sessions.map((s) => s.id), ['s1', 's2']);
  assert.equal(tree[1].name, '空项目');
  assert.equal(tree[1].sessions.length, 0);
  assert.equal(tree[2].key, UNGROUPED);
  assert.deepEqual(tree[2].sessions.map((s) => s.id), ['s3']);
});

test('inferCurrentProject:当前会话的项目优先,否则第一个项目', () => {
  const sessions = [{ id: 's1', project_id: 'p2' }];
  const projects = [
    { project_id: 'p1', name: 'A' },
    { project_id: 'p2', name: 'B' },
  ];
  assert.equal(inferCurrentProject('s1', sessions, projects), 'p2');
  assert.equal(inferCurrentProject(null, [], projects), 'p1');
  assert.equal(inferCurrentProject(null, [], []), null);
});

test('asProjectId:丢掉点击事件,只留字符串 id', () => {
  assert.equal(asProjectId('p-abc'), 'p-abc');
  assert.equal(asProjectId({ isTrusted: true, type: 'click' }), '');
  assert.equal(asProjectId(null), '');
  assert.equal(asProjectId(''), '');
});

test('isOpen:未记录时默认展开;记录折叠则关', () => {
  assert.equal(isOpen({}, 'p1'), true);
  assert.equal(isOpen({ p1: true }, 'p1'), false);
  assert.equal(isOpen({ p1: false }, 'p1'), true);
});
