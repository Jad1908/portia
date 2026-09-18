-- The native file chooser, for importing data that is outside the repo.
--
-- A file rather than a Python string for the same reason the CSS and the canvas
-- behaviour are files: this is code in another language, and it is easier to read
-- and to diff as itself. It also keeps it out of the way of the inline-prompt
-- rule, which exists for text the *model* reads and would otherwise flag a long
-- literal here for the wrong reason.
--
-- `POSIX path of` cannot be applied to a list, so the loop is how several paths
-- come out of one dialog. One per line; a cancelled dialog exits non-zero.
set picked to choose file with prompt "Choose data to import" with multiple selections allowed
set out to ""
repeat with f in picked
    set out to out & POSIX path of f & linefeed
end repeat
return out
