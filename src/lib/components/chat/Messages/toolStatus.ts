export const trimTrailingEllipsis = (value: string) =>
	(value ?? '').replace(/(?:\.\.\.|…)\s*$/, '').trim();

const TOOL_ACTION_LABELS: Record<string, string> = {
	web_search: 'Searching the web',
	web_search_preview: 'Searching the web',
	open_page: 'Opening page',
	find_in_page: 'Finding in page',
	exec_command: 'Running command',
	run_command: 'Running command',
	write_stdin: 'Sending input to session',
	list_files: 'Listing files',
	read_file: 'Reading file',
	download_artifact: 'Preparing artifact',
	view_image: 'Preparing image',
	apply_patch: 'Applying patch',
	write_file: 'Writing file',
	replace_file_content: 'Updating file',
	display_file: 'Preparing file preview'
};

const TOOL_DESCRIPTION_LABELS = new Set([
	'Searching the web',
	'Opening page',
	'Opened page',
	'Finding in page',
	'Running command',
	'Command finished',
	'Command still running',
	'Command failed',
	'Sending input to session',
	'Input sent',
	'Session update failed',
	'Listing files',
	'Files listed',
	'Reading file',
	'File read',
	'Preparing artifact',
	'Artifact ready',
	'Preparing image',
	'Image ready',
	'Applying patch',
	'Patch applied',
	'Writing file',
	'File written',
	'Updating file',
	'File updated',
	'Preparing file preview',
	'File preview ready',
	'Generated file'
]);

export const getLocalizedToolActionLabel = (
	action: string | null | undefined,
	translate: (key: string, options?: Record<string, unknown>) => string
) => {
	if (!action) {
		return '';
	}

	const key = TOOL_ACTION_LABELS[action];
	return key ? trimTrailingEllipsis(translate(key)) : '';
};

export const getLocalizedToolDescriptionLabel = (
	description: string | null | undefined,
	translate: (key: string, options?: Record<string, unknown>) => string
) => {
	if (!description || !TOOL_DESCRIPTION_LABELS.has(description)) {
		return '';
	}

	return trimTrailingEllipsis(translate(description));
};
