'use client';
import { useState } from 'react';

interface Page {
  id: number;
  url: string;
  title: string;
  status: string;
  url_path?: string;
  url_depth?: number;
  markdown_path?: string;
  screenshot_path?: string;
  media_items?: any[];
}

interface PageNode {
  segment: string;
  fullPath: string;
  page?: Page;
  children: PageNode[];
  isSyntheticFile?: boolean;
  actionType?: 'md' | 'img' | 'media';
  targetPath?: string;
}

function buildTree(pages: Page[]): PageNode {
  const root: PageNode = { segment: '/', fullPath: '/', children: [] };
  for (const page of pages) {
    const parts = (page.url_path || '/').split('/').filter(Boolean);
    let node = root;
    if (parts.length === 0) {
      root.page = page;
    } else {
      for (let i = 0; i < parts.length; i++) {
        let child = node.children.find(c => c.segment === parts[i] && !c.isSyntheticFile);
        if (!child) {
          child = {
            segment: parts[i],
            fullPath: '/' + parts.slice(0, i + 1).join('/'),
            children: [],
          };
          node.children.push(child);
        }
        if (i === parts.length - 1) child.page = page;
        node = child;
      }
    }
  }

  const injectSynthetic = (node: PageNode) => {
    if (node.page && node.page.status !== 'pending' && node.page.markdown_path) {
      node.children.push({
        segment: 'page.md',
        fullPath: node.fullPath + '/page.md',
        children: [],
        isSyntheticFile: true,
        actionType: 'md',
        targetPath: node.page.markdown_path
      });
      if (node.page.screenshot_path) {
        node.children.push({
          segment: 'screenshot.png',
          fullPath: node.fullPath + '/screenshot.png',
          children: [],
          isSyntheticFile: true,
          actionType: 'img',
          targetPath: node.page.screenshot_path
        });
      }
      if (node.page.media_items) {
        for (const media of node.page.media_items) {
          node.children.push({
            segment: media.local_path.split('/').pop() || 'media',
            fullPath: node.fullPath + '/' + media.local_path,
            children: [],
            isSyntheticFile: true,
            actionType: 'media',
            targetPath: media.local_path
          });
        }
      }
    }
    for (const c of node.children) {
      if (!c.isSyntheticFile) injectSynthetic(c);
    }
  };
  injectSynthetic(root);

  return root;
}

interface TreeNodeProps {
  node: PageNode;
  depth: number;
  darkMode: boolean;
  onOpenMd: (path: string) => void;
  onOpenImg: (path: string) => void;
  getDownloadUrl: (path: string) => string;
}

function TreeNode({ node, depth, darkMode: dm, onOpenMd, onOpenImg, getDownloadUrl }: TreeNodeProps) {
  const [expanded, setExpanded] = useState(depth < 2);
  const hasChildren = node.children.length > 0;
  const isRoot = node.segment === '/';

  const hoverBg = dm ? 'hover:bg-[#313244]/50' : 'hover:bg-[#ccd0da]/50';
  const textMuted = dm ? 'text-[#a6adc8]' : 'text-[#6c6f85]';
  const textMain  = dm ? 'text-[#cdd6f4]' : 'text-[#4c4f69]';

  return (
    <div>
      <div
        className={`flex items-center gap-1.5 px-2 py-1 rounded-md cursor-pointer select-none group ${hoverBg}`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
        onClick={(e) => {
          if (node.isSyntheticFile && node.targetPath) {
            e.stopPropagation();
            if (node.actionType === 'md') onOpenMd(node.targetPath);
            else if (node.actionType === 'img' || node.actionType === 'media') onOpenImg(node.targetPath);
          } else if (hasChildren) {
            setExpanded(prev => !prev);
          }
        }}
      >
        {/* Expand arrow */}
        {hasChildren ? (
          <span className={`material-symbols-outlined text-[14px] transition-transform ${textMuted} ${
            expanded ? 'rotate-90' : ''
          }`}>chevron_right</span>
        ) : (
          <span className="w-[14px]" />
        )}

        {/* Icon */}
        {node.page?.status === 'pending' || (node.page && !node.page.markdown_path && !hasChildren) ? (
          <span className={`material-symbols-outlined text-[16px] animate-spin ${textMuted}`}>sync</span>
        ) : (
          <span className={`material-symbols-outlined text-[16px] ${
            hasChildren
              ? (dm ? 'text-[#f9e2af]' : 'text-[#df8e1d]')
              : (node.isSyntheticFile 
                ? (node.actionType === 'md' ? (dm ? 'text-[#a6e3a1]' : 'text-[#40a02b]') : (dm ? 'text-[#89b4fa]' : 'text-[#1e66f5]'))
                : (dm ? 'text-[#89dceb]' : 'text-[#04a5e5]'))
          }`}>
            {hasChildren ? 'folder' : (node.isSyntheticFile ? (node.actionType === 'md' ? 'description' : 'image') : 'article')}
          </span>
        )}

        {/* Label */}
        <span className={`flex-1 text-xs truncate ${node.page?.status === 'pending' || (node.page && !node.page.markdown_path && !hasChildren) ? textMuted : textMain}`} title={node.page?.title || node.segment}>
          {node.page?.title || (isRoot ? '/' : node.segment)}
        </span>

        {/* Action buttons — show on hover (only for non-synthetic) */}
        {!node.isSyntheticFile && (
          <div className="flex items-center gap-1.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
            {node.page?.url && (
              <a
                href={node.page.url} target="_blank" rel="noreferrer"
                onClick={e => e.stopPropagation()}
                className={`flex items-center ${textMuted} hover:opacity-70`}

              title={node.page.url}
            >
              <span className="material-symbols-outlined text-[14px]">open_in_new</span>
            </a>
          )}
        </div>
        )}
      </div>

      {/* Children */}
      {hasChildren && expanded && (
        <div>
          {node.children.map(child => (
            <TreeNode
              key={child.fullPath}
              node={child}
              depth={depth + 1}
              darkMode={dm}
              onOpenMd={onOpenMd}
              onOpenImg={onOpenImg}
              getDownloadUrl={getDownloadUrl}
            />
          ))}
        </div>
      )}
    </div>
  );
}

interface PageTreeProps {
  pages: Page[];
  darkMode: boolean;
  onOpenMd: (path: string) => void;
  onOpenImg: (path: string) => void;
  getDownloadUrl: (path: string) => string;
}

export default function PageTree({ pages, darkMode, onOpenMd, onOpenImg, getDownloadUrl }: PageTreeProps) {
  if (!pages || pages.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-4 gap-2 text-center">
        <span className="material-symbols-outlined text-2xl">find_in_page</span>
        <p className={`text-xs ${darkMode ? 'text-[#a6adc8]' : 'text-[#6c6f85]'}`}>No content extracted.</p>
      </div>
    );
  }

  const tree = buildTree(pages);

  return (
    <div className="py-1">
      {/* Root node itself */}
      {tree.page && (
        <TreeNode
          node={tree}
          depth={0}
          darkMode={darkMode}
          onOpenMd={onOpenMd}
          onOpenImg={onOpenImg}
          getDownloadUrl={getDownloadUrl}
        />
      )}
      {/* Children of root */}
      {tree.children.map(child => (
        <TreeNode
          key={child.fullPath}
          node={child}
          depth={0}
          darkMode={darkMode}
          onOpenMd={onOpenMd}
          onOpenImg={onOpenImg}
          getDownloadUrl={getDownloadUrl}
        />
      ))}
    </div>
  );
}
