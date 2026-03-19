import type { Model } from '$lib/stores';

const collapseWhitespace = (value: string) => value.replace(/\s+/g, ' ').trim();

const stripMarkup = (value: string) =>
	collapseWhitespace(value.replace(/<[^>]*>/g, ' ').replace(/&nbsp;/g, ' '));

const truncate = (value: string, maxLength = 84) =>
	value.length > maxLength ? `${value.slice(0, maxLength - 3).trimEnd()}...` : value;

export const getModelDisplayName = (
	name?: string | null,
	id?: string | null,
	fallback = 'MODEL'
) => {
	const base = (name ?? id ?? fallback).trim();
	return (base || fallback).toUpperCase();
};

export const getModelShortDescription = (model?: Partial<Model> & Record<string, any>) => {
	const description = stripMarkup(model?.info?.meta?.description ?? '');
	if (description) {
		return truncate(description);
	}

	const capabilityLabels = [];
	if (model?.info?.meta?.capabilities?.vision) {
		capabilityLabels.push('vision');
	}
	if (model?.info?.meta?.capabilities?.web_search) {
		capabilityLabels.push('web');
	}
	if (model?.info?.meta?.capabilities?.code_interpreter) {
		capabilityLabels.push('tools');
	}

	let sourceLabel = 'Hosted chat model';
	if (model?.owned_by === 'ollama' || model?.connection_type === 'local') {
		sourceLabel = 'Local model';
	} else if (model?.direct) {
		sourceLabel = 'Direct API model';
	} else if (model?.connection_type === 'external') {
		sourceLabel = 'External API model';
	}

	return capabilityLabels.length > 0
		? `${sourceLabel} · ${capabilityLabels.slice(0, 2).join(' · ')}`
		: sourceLabel;
};
