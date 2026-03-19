import type { Model } from '$lib/stores';
import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';

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
	const normalized = (base || fallback).toLowerCase();

	if (normalized === 'gpt-5.4-pro') {
		return 'GPT-5.4-Pro';
	}

	return (base || fallback).toUpperCase();
};

const getKnownOpenAIModelDescription = (model?: Partial<Model> & Record<string, any>) => {
	const modelId = (model?.id ?? model?.name ?? '').toLowerCase();

	if (modelId === 'gpt-5.4') {
		return "Our most capable and efficient frontier model for professional work.";
	}

	if (modelId === 'gpt-5.4-pro') {
		return 'Our most intelligent model for research level questions and extremely complex tasks.';
	}

	return '';
};

export const getModelShortDescription = (model?: Partial<Model> & Record<string, any>) => {
	const knownDescription = getKnownOpenAIModelDescription(model);
	if (knownDescription) {
		return knownDescription;
	}

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

export const shouldUseOpenAILogo = (model?: Partial<Model> & Record<string, any>) => {
	const modelId = (model?.id ?? model?.name ?? '').toLowerCase();
	return model?.owned_by === 'openai' || modelId === 'gpt-5.4' || modelId === 'gpt-5.4-pro';
};

export const getModelAvatarSrc = (
	model?: Partial<Model> & Record<string, any>,
	lang = 'en'
) => {
	if (shouldUseOpenAILogo(model)) {
		return `${WEBUI_BASE_URL}/openai-mark.svg`;
	}

	const modelId = model?.id ?? model?.value ?? '';
	return `${WEBUI_API_BASE_URL}/models/model/profile/image?id=${modelId}&lang=${lang}`;
};
