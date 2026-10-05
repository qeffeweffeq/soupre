'use client';
import { useState } from 'react';
import PageTree from './PageTree';

interface PagesDialogProps {
  job: any;
  pages: any[];
  darkMode: boolean;
  onClose: () => void;
  onOpenMd: (path: string) => void;
  onOpenImg: (path: string) => void;
  getDownloadUrl: (path: string) => string;
}

export default function PagesDialog({ job, pages, darkMode, onClose, onOpenMd, onOpenImg, getDownloadUrl }: PagesDialogProps) {
  const isSitemap = job.scrape_mode === 'sitemap';
  const [view, setView] = useState<'list' | 'tree'>(isSitemap ? 'tree' : 'list');
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;
  
  const totalPages = Math.ceil(pages.length / itemsPerPage);
  const paginatedPages = pages.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage);

  const bg = darkMode ? 'bg-[#1e1e2e] text-[#cdd6f4] border-[#313244]' : 'bg-white text-[#4c4f69] border-[#ccd0da]';
  const headerBg = darkMode ? 'bg-[#181825] border-[#313244]' : 'bg-[#e6e9ef] border-[#ccd0da]';
  
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose}></div>
      <div className={`relative w-full max-w-4xl max-h-[85vh] flex flex-col rounded-xl shadow-2xl border overflow-hidden ${bg}`}>
        
        {/* Header */}
        <div className={`flex items-center justify-between p-4 border-b shrink-0 ${headerBg}`}>
          <div className="flex items-center gap-4">
            <h3 className="font-bold flex items-center gap-2">
              <span className="material-symbols-outlined text-[18px]">find_in_page</span>
              All Pages ({pages.length})
            </h3>
            
            {/* View Toggles */}
            <div className={`flex rounded-lg p-1 gap-1 ${darkMode ? 'bg-[#11111b]' : 'bg-[#ccd0da]'}`}>
              <button onClick={() => setView('list')} className={`flex items-center gap-1.5 px-3 py-1 text-xs font-bold rounded-md transition-all ${view === 'list' ? (darkMode ? 'bg-[#313244] text-[#cdd6f4]' : 'bg-white text-[#4c4f69]') : 'opacity-60 hover:opacity-100'}`}>
                <span className="material-symbols-outlined text-[14px]">list</span> List
              </button>
              <button onClick={() => setView('tree')} className={`flex items-center gap-1.5 px-3 py-1 text-xs font-bold rounded-md transition-all ${view === 'tree' ? (darkMode ? 'bg-[#313244] text-[#cdd6f4]' : 'bg-white text-[#4c4f69]') : 'opacity-60 hover:opacity-100'}`}>
                <span className="material-symbols-outlined text-[14px]">account_tree</span> Tree
              </button>
            </div>
          </div>
          
          <button onClick={onClose} className={`cursor-pointer w-7 h-7 flex items-center justify-center rounded-md transition-colors ${darkMode ? 'hover:bg-[#313244]' : 'hover:bg-[#ccd0da]'}`}>
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>

        {/* Content */}
        <div className="p-4 overflow-y-auto flex-1">
          {view === 'tree' ? (
            <PageTree pages={pages} darkMode={darkMode} onOpenMd={onOpenMd} onOpenImg={onOpenImg} getDownloadUrl={getDownloadUrl} />
          ) : (
            <ul className={`flex flex-col divide-y ${darkMode ? 'divide-[#313244]' : 'divide-[#ccd0da]'}`}>
              {paginatedPages.map(page => (
                <li key={page.id} className="flex items-center justify-between py-3">
                  <a href={page.url} target="_blank" rel="noreferrer" className={`flex items-center gap-2 text-sm font-medium truncate mr-4 ${darkMode ? 'text-[#cdd6f4]' : 'text-[#4c4f69]'} hover:underline`}>
                    <span className="material-symbols-outlined text-[18px]">link</span>
                    <span className="truncate" title={page.title || page.url}>{page.title || page.url}</span>
                  </a>
                  <div className="flex items-center gap-3 shrink-0">
                    {page.status === 'pending' || !page.markdown_path ? (
                      <span className={`text-xs font-bold flex items-center gap-1 ${darkMode ? 'text-[#a6adc8]' : 'text-[#6c6f85]'}`}>
                        <span className="material-symbols-outlined text-[14px] animate-spin">sync</span> Pending...
                      </span>
                    ) : (
                      <>
                        <button onClick={() => onOpenMd(page.markdown_path)} className={`flex items-center gap-1 text-xs font-bold hover:opacity-70 ${darkMode ? 'text-[#a6e3a1]' : 'text-[#40a02b]'}`}>
                          <span className="material-symbols-outlined text-[18px]">visibility</span> MD
                        </button>
                        {page.screenshot_path && (
                          <button onClick={() => onOpenImg(page.screenshot_path)} className={`flex items-center gap-1 text-xs font-bold hover:opacity-70 ${darkMode ? 'text-[#89b4fa]' : 'text-[#1e66f5]'}`}>
                            <span className="material-symbols-outlined text-[18px]">image</span> IMG
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Pagination Footer (only for list view) */}
        {view === 'list' && totalPages > 1 && (
          <div className={`flex items-center justify-between p-4 border-t shrink-0 ${headerBg}`}>
            <button onClick={() => setCurrentPage(p => Math.max(1, p - 1))} disabled={currentPage === 1} className="px-3 py-1.5 text-xs font-bold bg-[#89b4fa] text-[#11111b] disabled:opacity-50 rounded">Prev</button>
            <span className="text-xs font-bold">Page {currentPage} of {totalPages}</span>
            <button onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))} disabled={currentPage === totalPages} className="px-3 py-1.5 text-xs font-bold bg-[#89b4fa] text-[#11111b] disabled:opacity-50 rounded">Next</button>
          </div>
        )}
      </div>
    </div>
  );
}
