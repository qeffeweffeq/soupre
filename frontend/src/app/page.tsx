'use client';
import { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';

import SettingsDialog, { loadSettings, saveSettings, DEFAULT_SETTINGS, Settings } from './components/SettingsDialog';
import PageTree from './components/PageTree';
import PagesDialog from './components/PagesDialog';

import remarkGfm from 'remark-gfm';
import { Sidebar, ThemeToggle, Card } from '@qfwfq/material-catppuccin-ui';




export default function Home() {
  const [url, setUrl] = useState('');
  const [jobs, setJobs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  // Default to true for dark mode as requested by "dark mode"
  
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  useEffect(() => { setSettings(loadSettings()); }, []);
  const darkMode = settings.darkMode;
  const [scrapeMode, setScrapeMode] = useState<'single' | 'sitemap'>('single');

  
  const [selectedJob, setSelectedJob] = useState<any>(null);
  const [jobPages, setJobPages] = useState<any[]>([]);
  const [loadingPages, setLoadingPages] = useState(false);
  const [jobLogs, setJobLogs] = useState<string[]>([]);
  const [markdownContent, setMarkdownContent] = useState<string>('');
  const [markdownBaseUrl, setMarkdownBaseUrl] = useState<string>('');
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [isPagesModalOpen, setIsPagesModalOpen] = useState(false);

  const [isImagePreviewOpen, setIsImagePreviewOpen] = useState(false);
  const [imagePreviewUrl, setImagePreviewUrl] = useState('');

  const openImagePreview = (path: string) => {
    setImagePreviewUrl(getDownloadUrl(path));
    setIsImagePreviewOpen(true);
  };


  const handleDeleteJob = async (e: React.MouseEvent, jobId: string) => {
    e.stopPropagation();
    try {
      await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/jobs/${jobId}`, { method: 'DELETE' });
      fetchJobs();
      if (selectedJob?.id === jobId) {
        setSelectedJob(null);
        setJobPages([]);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleCancelJob = async (e: React.MouseEvent, jobId: string) => {
    e.stopPropagation();
    try {
      await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/jobs/${jobId}/cancel`, { method: 'POST' });
      fetchJobs();
    } catch (e) {
      console.error(e);
    }
  };

  const handleResumeJob = async (e: React.MouseEvent, jobId: string) => {
    e.stopPropagation();
    try {
      await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/jobs/${jobId}/resume`, { method: 'POST' });
      fetchJobs();
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    let eventSource: EventSource | null = null;

    if (selectedJob && selectedJob.status !== 'completed' && selectedJob.status !== 'failed') {
      setJobLogs([]);
      eventSource = new EventSource(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/jobs/${selectedJob.id}/logs`);
      
      eventSource.onmessage = async (event) => {
        if (event.data === '__DONE__' || event.data === 'done') {
          eventSource?.close();
          fetchJobs();
          
          // Automatically fetch the pages now that it's completed
          setLoadingPages(true);
          try {
            const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/jobs/${selectedJob.id}/pages`);
            if (res.ok) {
              const data = await res.json();
              setJobPages(data);
            }
          } catch (e) {
            console.error(e);
          } finally {
            setLoadingPages(false);
          }
          
          return;
        }
        
        if (event.data !== 'keep-alive') {
          setJobLogs(prev => [...prev, event.data]);
        }
      };

      eventSource.onerror = () => {
        eventSource?.close();
      };
    }

    return () => {
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [selectedJob]);

  const openMarkdownPreview = async (path: string) => {
    const fullUrl = getDownloadUrl(path);
    // Derive the base directory URL so relative images resolve correctly
    const baseUrl = fullUrl.substring(0, fullUrl.lastIndexOf('/') + 1);
    setMarkdownBaseUrl(baseUrl);
    setIsPreviewOpen(true);
    setPreviewLoading(true);
    try {
      const res = await fetch(fullUrl);
      if (res.ok) {
        const text = await res.text();
        setMarkdownContent(text);
      } else {
        setMarkdownContent('Failed to load markdown content.');
      }
    } catch (e) {
      setMarkdownContent('Error loading markdown content.');
    } finally {
      setPreviewLoading(false);
    }
  };


  const fetchJobs = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/jobs`);
      if (res.ok) {
        const data = await res.json();
        setJobs(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchJobs();
    const interval = setInterval(fetchJobs, 5000);
    return () => clearInterval(interval);
  }, []);
  
  // Apply catppuccin colors to body
  useEffect(() => {
    if (darkMode) {
      document.documentElement.classList.add('dark');
      document.body.style.backgroundColor = '#1e1e2e'; // Mocha Base
    } else {
      document.documentElement.classList.remove('dark');
      document.body.style.backgroundColor = '#eff1f5'; // Latte Base
    }
  }, [darkMode]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/jobs?` +
          new URLSearchParams({
            target_url: url,
            scrape_mode: scrapeMode,
            max_pages: String(settings.maxPages),
            delay_min: String(settings.delayMin),
            delay_max: String(settings.delayMax),
            large_threshold: String(settings.largeThreshold),
            large_pause_sec: String(settings.largePauseSec),
          }),
        { method: 'POST' }
      );
      setUrl('');
      await fetchJobs();
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleJobClick = async (job: any) => {
    if (selectedJob?.id === job.id) {
      setSelectedJob(null);
      return;
    }
    setSelectedJob(job);
    setJobLogs([]);
    if (job.status === 'completed') {
      setLoadingPages(true);
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/jobs/${job.id}/pages`);
        if (res.ok) {
          const data = await res.json();
          setJobPages(data);
        }
      } catch (e) {
        console.error(e);
      } finally {
        setLoadingPages(false);
      }
    } else {
      setJobPages([]);
    }
  };

  const getDownloadUrl = (localPath: string) => {
    if (!localPath) return '';
    return localPath.replace('../_downloads', `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/downloads`);
  };

  return (
    <div className={`min-h-screen flex transition-colors duration-200 ${darkMode ? 'bg-[#1e1e2e] text-[#cdd6f4]' : 'bg-[#eff1f5] text-[#4c4f69]'}`}>
      
      {/* Sidebar */}
      <Sidebar darkMode={darkMode} title="Souprè" subtitle="Web Archiving">
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="relative">
      <span className="material-symbols-outlined text-[18px] absolute left-3 top-2.5">
              link
            </span>
            <input 
              type="url" 
              placeholder="https://example.com"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className={`w-full border rounded-lg pl-10 pr-3 py-2 text-sm outline-none transition-colors ${
                darkMode 
                  ? 'bg-[#11111b] border-[#313244] text-[#cdd6f4] placeholder-[#7f849c] focus:border-[#89b4fa] focus:ring-1 focus:ring-[#89b4fa]' 
                  : 'bg-white border-[#ccd0da] text-[#4c4f69] placeholder-[#9ca0b0] focus:border-[#1e66f5] focus:ring-1 focus:ring-[#1e66f5]'
              }`}
              required
              disabled={loading}
            />
          </div>

          {/* Segmented scrape mode toggle */}
          <div className={`flex rounded-lg p-1 gap-1 ${darkMode ? 'bg-[#11111b]' : 'bg-[#ccd0da]'}`}>
            {(['single', 'sitemap'] as const).map(mode => (
              <button
                key={mode}
                type="button"
                onClick={() => setScrapeMode(mode)}
                disabled={loading}
                className={`flex-1 flex items-center justify-center gap-1.5 rounded-md py-1.5 text-xs font-bold transition-all disabled:opacity-50 ${
                  scrapeMode === mode
                    ? darkMode
                      ? 'bg-[#313244] text-[#cdd6f4] shadow-sm'
                      : 'bg-white text-[#4c4f69] shadow-sm'
                    : darkMode ? 'text-[#6c6f85] hover:text-[#cdd6f4]' : 'text-[#9ca0b0] hover:text-[#4c4f69]'
                }`}
              >
                <span className="material-symbols-outlined text-[15px]">
                  {mode === 'single' ? 'article' : 'account_tree'}
                </span>
                {mode === 'single' ? 'Single Page' : 'Full Site'}
              </button>
            ))}
          </div>

          <button 
            type="submit"
            disabled={loading}
            className={`cursor-pointer disabled:cursor-not-allowed flex items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-bold shadow-sm transition-all ${
              darkMode
                ? 'bg-[#89b4fa] text-[#11111b] hover:bg-[#b4befe] disabled:bg-[#45475a] disabled:text-[#a6adc8]'
                : 'bg-[#1e66f5] text-white hover:bg-[#7287fd] disabled:bg-[#ccd0da] disabled:text-[#8c8fa1]'
            }`}
          >
            {loading ? (
              <>
        <span className="material-symbols-outlined text-[18px] animate-spin">sync</span>
                Starting...
              </>
            ) : (
              <>
                <span className="material-symbols-outlined text-[18px]">
                  {scrapeMode === 'sitemap' ? 'travel_explore' : 'rocket_launch'}
                </span>
                {scrapeMode === 'sitemap' ? 'Slurp Entire Site' : 'Start Scraping'}
              </>
            )}
          </button>
        </form>
        <div className="mt-auto flex justify-start pt-8">
          <SettingsDialog settings={settings} onChange={s => { setSettings(s); saveSettings(s); }} />
        </div>
      </Sidebar>

      {/* Main Content */}
      <main className="flex-1 ml-80 pt-6 px-8 pb-8 min-h-screen">
        <div className="flex items-center gap-2 mb-8">
          <h2 className={`text-2xl font-bold ${darkMode ? 'text-[#cdd6f4]' : 'text-[#4c4f69]'}`}>Recent Jobs</h2>
        </div>
        
        <div className="grid grid-cols-1 xl:grid-cols-2 2xl:grid-cols-3 gap-6">
          {jobs.map((job: any) => (
            <div key={job.id} className="flex flex-col gap-4">
              <Card darkMode={darkMode} selected={selectedJob?.id === job.id}>
                {/* Card Header (clickable) */}
                <div onClick={() => handleJobClick(job)} className="p-5 flex flex-col gap-3 cursor-pointer">
                  <div className="flex items-center justify-between gap-4 w-full">
                    <div className="flex items-center gap-2 overflow-hidden">
                      <span className={`material-symbols-outlined text-[18px] flex-shrink-0 ${darkMode ? 'text-[#45475a]' : 'text-[#ccd0da]'}`}>
                        language
                      </span>
                      <span className={`text-sm font-semibold truncate ${darkMode ? 'text-[#cdd6f4]' : 'text-[#4c4f69]'}`} title={job.target_url}>
                        {job.target_url}
                      </span>
                    </div>
                    {/* Delete Button */}
                    <button 
                      onClick={(e) => handleDeleteJob(e, job.id)}
                      className={`cursor-pointer flex-shrink-0 p-1 -mr-1 opacity-0 group-hover:opacity-100 transition-colors ${darkMode ? 'text-[#a6adc8] hover:text-[#f38ba8]' : 'text-[#6c6f85] hover:text-[#d20f39]'}`}
                      title="Delete Job"
                    >
                      <span className="material-symbols-outlined text-[18px]">delete</span>
                    </button>
                  </div>
                  
                  <div className="flex items-center justify-between mt-1">
                    <span className={`text-xs px-2.5 py-1 rounded-full font-bold flex items-center gap-1 ${
                      job.status === 'completed' ? (darkMode ? 'bg-[#a6e3a1]/20 text-[#a6e3a1]' : 'bg-[#40a02b]/15 text-[#40a02b]') : 
                      job.status === 'failed' ? (darkMode ? 'bg-[#f38ba8]/20 text-[#f38ba8]' : 'bg-[#d20f39]/15 text-[#d20f39]') :
                      (darkMode ? 'bg-[#f9e2af]/20 text-[#f9e2af]' : 'bg-[#df8e1d]/15 text-[#df8e1d]')
                    }`}>
                      <span className="material-symbols-outlined text-[18px]">
                        {job.status === 'completed' ? 'check_circle' : job.status === 'failed' ? 'error' : 'hourglass_bottom'}
                      </span>
                      {job.status}
                    </span>
                    {job.status === 'running' && (
                      <button
                        onClick={(e) => handleCancelJob(e, job.id)}
                        className={`cursor-pointer ml-1 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase transition-colors opacity-80 hover:opacity-100 ${
                          darkMode ? 'bg-[#f38ba8] text-[#11111b] hover:bg-[#eba0ac]' : 'bg-[#d20f39] text-white hover:bg-[#e64553]'
                        }`}
                        title="Stop Processing"
                      >
                        <span className="material-symbols-outlined text-[11px]">stop</span> Stop
                      </button>
                    )}
                    {job.status !== 'running' && job.scrape_mode === 'sitemap' && (
                      <button
                        onClick={(e) => handleResumeJob(e, job.id)}
                        className={`cursor-pointer ml-1 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase transition-colors opacity-80 hover:opacity-100 ${
                          darkMode ? 'bg-[#89b4fa] text-[#11111b] hover:bg-[#b4befe]' : 'bg-[#1e66f5] text-white hover:bg-[#7287fd]'
                        }`}
                        title="Resume Processing"
                      >
                        <span className="material-symbols-outlined text-[11px]">play_arrow</span> Resume
                      </button>
                    )}
                    <span className={`text-xs flex items-center gap-1 ${darkMode ? 'text-[#a6adc8]' : 'text-[#6c6f85]'}`}>
                      <span className="material-symbols-outlined text-[18px]">schedule</span>
                      {new Date(job.created_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}
                    </span>
                  </div>
                </div>

                {/* Minimal List Preview Panel */}
                {selectedJob?.id === job.id && (
                  <div className={`border-t px-5 py-2 animate-in fade-in slide-in-from-top-1 ${darkMode ? 'border-[#313244]' : 'border-[#ccd0da]'}`}>
                    {job.status !== 'completed' && job.status !== 'failed' ? (
                      <div className="flex flex-col gap-2 py-3">
                        <div className="flex items-center gap-2">
                          <span className="material-symbols-outlined animate-spin text-[16px]">autorenew</span>
                          <span className={`text-xs font-semibold ${darkMode ? 'text-[#cdd6f4]' : 'text-[#4c4f69]'}`}>
                            Scraping in progress...
                          </span>
                        </div>
                        <div className={`mt-2 rounded p-3 h-48 overflow-y-auto text-[10px] font-mono leading-tight whitespace-pre-wrap ${darkMode ? 'bg-[#11111b] text-[#a6adc8]' : 'bg-[#e6e9ef] text-[#5c5f77]'}`}>
                          {jobLogs.length === 0 ? (
                            <span className="opacity-50">Waiting for logs...</span>
                          ) : (
                            jobLogs.map((log, i) => (
                              <div key={i} className="mb-1">{log}</div>
                            ))
                          )}
                        </div>
                      </div>
                    ) : job.status === 'failed' ? (
                      <div className="flex flex-col items-center justify-center py-4 gap-2 text-center">
                        <span className="material-symbols-outlined text-2xl text-[#f38ba8]">error</span>
                        <p className={`text-xs font-medium ${darkMode ? 'text-[#a6adc8]' : 'text-[#6c6f85]'}`}>
                          Job failed. No details available.
                        </p>
                      </div>
                    ) : loadingPages ? (
                      <div className="flex items-center justify-center py-4">
                        <span className="material-symbols-outlined animate-spin text-[18px]">
                          autorenew
                        </span>
                      </div>
                    ) : jobPages.length > 0 ? (
                      <div className="flex flex-col">
                        <ul className={`flex flex-col divide-y ${darkMode ? 'divide-[#313244]' : 'divide-[#ccd0da]'}`}>
                          {jobPages.slice(0, 3).map((page: any) => (
                            <li key={page.id} className="flex items-center justify-between py-3">
                              <a href={page.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className={`cursor-pointer flex items-center gap-2 text-sm font-medium truncate mr-4 ${darkMode ? 'text-[#cdd6f4]' : 'text-[#4c4f69]'}`}>
                                <span className="material-symbols-outlined text-[18px]">link</span>
                                <span className="truncate max-w-[200px] hover:underline" title={page.title || page.url}>{page.title || page.url}</span>
                              </a>
                              <div className="flex items-center gap-3 shrink-0">
                                {page.markdown_path && (
                                  <button 
                                    onClick={(e) => { e.stopPropagation(); openMarkdownPreview(page.markdown_path); }} 
                                    className={`cursor-pointer flex items-center gap-1 text-xs font-bold transition-opacity hover:opacity-70 ${darkMode ? 'text-[#a6e3a1]' : 'text-[#40a02b]'}`}
                                  >
                                    <span className="material-symbols-outlined text-[18px]">visibility</span> MD
                                  </button>
                                )}
                                {page.screenshot_path && (
                                  <button 
                                    onClick={(e) => { e.stopPropagation(); openImagePreview(page.screenshot_path); }}
                                    className={`cursor-pointer flex items-center gap-1 text-xs font-bold transition-opacity hover:opacity-70 ${darkMode ? 'text-[#89b4fa]' : 'text-[#1e66f5]'}`}
                                  >
                                    <span className="material-symbols-outlined text-[18px]">image</span> IMG
                                  </button>
                                )}
                              </div>
                            </li>
                          ))}
                        </ul>
                        {jobPages.length > 3 && (
                          <button
                            onClick={(e) => { e.stopPropagation(); setIsPagesModalOpen(true); }}
                            className={`mt-2 py-2 text-xs font-bold text-center rounded transition-colors ${darkMode ? 'bg-[#313244] hover:bg-[#45475a] text-[#bac2de]' : 'bg-[#e6e9ef] hover:bg-[#ccd0da] text-[#5c5f77]'}`}
                          >
                            View all {jobPages.length} pages
                          </button>
                        )}
                      </div>
                    ) : (
                      <div className="flex flex-col items-center justify-center py-4 gap-2 text-center">
                        <span className="material-symbols-outlined text-2xl">
                          find_in_page
                        </span>
                        <p className={`text-xs ${darkMode ? 'text-[#a6adc8]' : 'text-[#6c6f85]'}`}>No content extracted.</p>
                      </div>
                    )}
                  </div>
                )}
              </Card>
            </div>
          ))}
          
          {jobs.length === 0 && (
            <div className="col-span-full flex flex-col items-center justify-center py-20 gap-4">
       <div className="text-6xl leading-none mb-4 mix-blend-luminosity">🍝</div>
              <p className={`text-lg font-medium ${darkMode ? 'text-[#45475a]' : 'text-[#9ca0b0]'}`}>
                No soups yet. Enter a URL to start slurping!
              </p>
            </div>
          )}
        </div>
      </main>

      {/* Markdown Preview Dialog */}
      {isPreviewOpen && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 sm:p-6">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setIsPreviewOpen(false)}></div>
          <div className={`relative w-full max-w-4xl max-h-full flex flex-col rounded-xl shadow-2xl overflow-hidden ${darkMode ? 'bg-[#1e1e2e] text-[#cdd6f4] border border-[#313244]' : 'bg-white text-[#4c4f69] border border-[#ccd0da]'}`}>
            <div className={`flex items-center justify-between p-4 border-b ${darkMode ? 'border-[#313244] bg-[#181825]' : 'border-[#ccd0da] bg-[#e6e9ef]'}`}>
              <h3 className="font-bold flex items-center gap-2">
                <span className="material-symbols-outlined text-[18px]">visibility</span>
                Markdown Preview
              </h3>
              <button 
                onClick={() => setIsPreviewOpen(false)}
                className={`cursor-pointer w-7 h-7 flex items-center justify-center rounded-md transition-colors ${darkMode ? 'hover:bg-[#313244] text-[#bac2de]' : 'hover:bg-[#ccd0da] text-[#5c5f77]'}`}
              >
                <span className="material-symbols-outlined text-[18px]">close</span>
              </button>
            </div>
            <div className="p-6 overflow-auto flex-1">
              {previewLoading ? (
                <div className="flex justify-center items-center h-32">
                  <span className="material-symbols-outlined animate-spin text-[24px]">autorenew</span>
                </div>
              ) : (
                <article className={`prose max-w-none ${darkMode ? 'prose-invert prose-pre:bg-[#181825] prose-pre:border prose-pre:border-[#313244]' : 'prose-pre:bg-[#e6e9ef] prose-pre:border prose-pre:border-[#ccd0da]'}`}>
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      img: ({ src, alt, ...props }) => {
                        // Resolve relative image paths against the markdown file's directory
                        const resolvedSrc = (typeof src === 'string') && !src.startsWith('http') && !src.startsWith('data:')
                          ? markdownBaseUrl + src
                          : src;
                        return (
                          <img
                            src={resolvedSrc}
                            alt={alt || ''}
                            className="max-w-full rounded shadow my-4"
                            {...props}
                          />
                        );
                      },
                    }}
                  >{markdownContent}</ReactMarkdown>
                </article>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Image Preview Dialog */}
      {isImagePreviewOpen && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 sm:p-6">
          <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={() => setIsImagePreviewOpen(false)}></div>
          <div className={`relative w-full max-w-5xl max-h-full flex flex-col rounded-xl shadow-2xl overflow-hidden ${darkMode ? 'bg-[#1e1e2e] border border-[#313244]' : 'bg-[#e6e9ef] border border-[#ccd0da]'}`}>
            <div className={`flex items-center justify-between p-4 border-b ${darkMode ? 'border-[#313244] bg-[#181825]' : 'border-[#ccd0da] bg-white'}`}>
              <h3 className={`font-bold flex items-center gap-2 ${darkMode ? 'text-[#cdd6f4]' : 'text-[#4c4f69]'}`}>
                <span className="material-symbols-outlined text-[18px]">image</span>
                Screenshot Preview
              </h3>
              <div className="flex items-center gap-3">
                <a 
                  href={imagePreviewUrl} 
                  target="_blank" 
                  rel="noreferrer"
                  className={`cursor-pointer w-7 h-7 flex items-center justify-center rounded-md transition-colors ${darkMode ? 'hover:bg-[#313244] text-[#89b4fa]' : 'hover:bg-[#ccd0da] text-[#1e66f5]'}`}
                  title="Open Original"
                >
                  <span className="material-symbols-outlined text-[18px]">open_in_new</span>
                </a>
                <button 
                  onClick={() => setIsImagePreviewOpen(false)}
                  className={`cursor-pointer w-7 h-7 flex items-center justify-center rounded-md transition-colors ${darkMode ? 'hover:bg-[#313244] text-[#bac2de]' : 'hover:bg-[#ccd0da] text-[#5c5f77]'}`}
                >
                  <span className="material-symbols-outlined text-[18px]">close</span>
                </button>
              </div>
            </div>
            <div className="p-4 flex-1 overflow-auto bg-black/5">
              <img 
                src={imagePreviewUrl} 
                alt="Screenshot Preview" 
                className="w-full h-auto shadow-sm rounded border border-black/10"
              />
            </div>
          </div>
        </div>
      )}


      {isPagesModalOpen && selectedJob && (
        <PagesDialog
          job={selectedJob}
          pages={jobPages}
          darkMode={darkMode}
          onClose={() => setIsPagesModalOpen(false)}
          onOpenMd={openMarkdownPreview}
          onOpenImg={openImagePreview}
          getDownloadUrl={getDownloadUrl}
        />
      )}

    </div>
  );
}
