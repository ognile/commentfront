import { useState, useRef, useMemo, useEffect } from 'react';
import { Search, Plus, X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

interface TagInputProps {
  allTags: string[];
  selectedTags: string[];
  onTagAdd: (tag: string) => void;
  onTagRemove?: (tag: string) => void;
  placeholder?: string;
  allowCreate?: boolean;
  size?: 'sm' | 'md';
  showSelectedAsBadges?: boolean;
  disabled?: boolean;
}

export function TagInput({
  allTags,
  selectedTags,
  onTagAdd,
  onTagRemove,
  placeholder = 'Search tags...',
  allowCreate = true,
  size = 'md',
  showSelectedAsBadges = false,
  disabled = false,
}: TagInputProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Filter tags based on search, exclude already selected, and sort alphabetically
  const filteredTags = useMemo(() => {
    const available = allTags.filter(t => !selectedTags.includes(t));
    const sorted = available.sort((a, b) => a.localeCompare(b));
    if (!search) return sorted;
    const lower = search.toLowerCase();
    return sorted.filter(t => t.toLowerCase().includes(lower));
  }, [allTags, selectedTags, search]);

  // Check if we should show the "Create" option
  const normalizedSearch = search.trim().toLowerCase();
  const showCreateOption = allowCreate && normalizedSearch && !allTags.includes(normalizedSearch);

  // Total items in dropdown (filtered tags + optional create)
  const totalItems = filteredTags.length + (showCreateOption ? 1 : 0);

  // Handle click outside to close
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
        setSearch('');
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Reset highlighted index when filtered results change
  useEffect(() => {
    setHighlightedIndex(0);
  }, [filteredTags.length, showCreateOption]);

  const handleSelectTag = (tag: string) => {
    onTagAdd(tag);
    setSearch('');
    setHighlightedIndex(0);
    // Keep focus on input for adding more tags
    inputRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setIsOpen(false);
      setSearch('');
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlightedIndex(prev => Math.min(prev + 1, totalItems - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlightedIndex(prev => Math.max(prev - 1, 0));
    } else if (e.key === 'Enter' && isOpen) {
      e.preventDefault();
      if (highlightedIndex < filteredTags.length) {
        handleSelectTag(filteredTags[highlightedIndex]);
      } else if (showCreateOption) {
        handleSelectTag(normalizedSearch);
      }
    }
  };

  const sizeClasses = size === 'sm'
    ? 'h-6 px-2 text-xs min-w-[90px]'
    : 'h-10 px-4 text-sm min-w-[180px]';

  const iconSize = size === 'sm' ? 'w-3 h-3' : 'w-4 h-4';

  return (
    <div ref={containerRef} className="relative inline-block">
      <div className="flex items-center gap-2 flex-wrap">
        {/* Selected tags as badges (when showSelectedAsBadges is true), sorted alphabetically */}
        {showSelectedAsBadges && [...selectedTags].sort().map(tag => (
          <Badge
            key={tag}
            variant="default"
            className={cn(
              "cursor-pointer group",
              size === 'sm' ? 'text-[10px] py-0 h-5' : 'text-xs'
            )}
            onClick={() => onTagRemove?.(tag)}
          >
            {tag}
            <X className={cn("ml-1 opacity-70 group-hover:opacity-100", size === 'sm' ? 'w-2 h-2' : 'w-3 h-3')} />
          </Badge>
        ))}

        {/* Search input */}
        <div className="relative">
          <div className="absolute left-2 top-1/2 -translate-y-1/2 pointer-events-none text-[#999999]">
            {size === 'sm' ? (
              <Plus className={iconSize} />
            ) : (
              <Search className={iconSize} />
            )}
          </div>
          <input
            ref={inputRef}
            type="text"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              if (!isOpen) setIsOpen(true);
            }}
            onFocus={() => setIsOpen(true)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={disabled}
            className={cn(
              "rounded-full border border-[rgba(0,0,0,0.1)] bg-white text-[#111111]",
              "placeholder:text-[#999999] focus:outline-none focus:border-[#333333]",
              "transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
              sizeClasses,
              size === 'sm' ? 'pl-6' : 'pl-10'
            )}
          />

          {/* Dropdown */}
          {isOpen && !disabled && (
            <div className={cn(
              "absolute top-full left-0 mt-1 w-full max-h-48 overflow-auto",
              "rounded-[16px] border border-[rgba(0,0,0,0.1)] bg-white shadow-lg z-50",
              size === 'sm' ? 'min-w-[150px]' : 'min-w-[200px]'
            )}>
              {totalItems === 0 ? (
                <div className={cn(
                  "px-3 py-2 text-[#999999]",
                  size === 'sm' ? 'text-xs' : 'text-sm'
                )}>
                  {allTags.length === 0 ? 'No tags yet' : 'No matching tags'}
                </div>
              ) : (
                <>
                  {filteredTags.map((tag, i) => (
                    <div
                      key={tag}
                      onClick={() => handleSelectTag(tag)}
                      className={cn(
                        "px-3 py-2 cursor-pointer",
                        size === 'sm' ? 'text-xs' : 'text-sm',
                        highlightedIndex === i
                          ? 'bg-[rgba(51,51,51,0.08)]'
                          : 'hover:bg-[rgba(51,51,51,0.05)]'
                      )}
                    >
                      {tag}
                    </div>
                  ))}
                  {showCreateOption && (
                    <div
                      onClick={() => handleSelectTag(normalizedSearch)}
                      className={cn(
                        "px-3 py-2 cursor-pointer text-[#666666] border-t border-[rgba(0,0,0,0.1)]",
                        "flex items-center gap-1",
                        size === 'sm' ? 'text-xs' : 'text-sm',
                        highlightedIndex === filteredTags.length
                          ? 'bg-[rgba(51,51,51,0.08)]'
                          : 'hover:bg-[rgba(51,51,51,0.05)]'
                      )}
                    >
                      <Plus className={cn(size === 'sm' ? 'w-3 h-3' : 'w-4 h-4')} />
                      Create "{normalizedSearch}"
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
