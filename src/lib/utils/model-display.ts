import type { Model } from '$lib/stores';
import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';

type TranslateFn = (key: string, options?: Record<string, unknown>) => string;

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

	return (base || fallback).toUpperCase();
};

const translateLabel = (translate: TranslateFn | undefined, value: string) =>
	translate ? translate(value) : value;

const getKnownOpenAIModelDescription = (
	model?: Partial<Model> & Record<string, any>,
	translate?: TranslateFn
) => {
	const modelId = (model?.id ?? model?.name ?? '').toLowerCase();

	if (modelId === 'gpt-5.5') {
		return translateLabel(
			translate,
			'Our most capable and efficient frontier model for professional work.'
		);
	}

	return '';
};

export const getModelShortDescription = (
	model?: Partial<Model> & Record<string, any>,
	translate?: TranslateFn
) => {
	const knownDescription = getKnownOpenAIModelDescription(model, translate);
	if (knownDescription) {
		return knownDescription;
	}

	const description = stripMarkup(model?.info?.meta?.description ?? '');
	if (description) {
		return truncate(translateLabel(translate, description));
	}

	const capabilityLabels = [];
	if (model?.info?.meta?.capabilities?.vision) {
		capabilityLabels.push(translateLabel(translate, 'vision'));
	}
	if (model?.info?.meta?.capabilities?.web_search) {
		capabilityLabels.push(translateLabel(translate, 'web'));
	}
	if (model?.info?.meta?.capabilities?.code_interpreter) {
		capabilityLabels.push(translateLabel(translate, 'tools'));
	}

	let sourceLabel = translateLabel(translate, 'Hosted chat model');
	if (model?.owned_by === 'ollama' || model?.connection_type === 'local') {
		sourceLabel = translateLabel(translate, 'Local model');
	} else if (model?.direct) {
		sourceLabel = translateLabel(translate, 'Direct API model');
	} else if (model?.connection_type === 'external') {
		sourceLabel = translateLabel(translate, 'External API model');
	}

	return capabilityLabels.length > 0
		? `${sourceLabel} · ${capabilityLabels.slice(0, 2).join(' · ')}`
		: sourceLabel;
};

export const shouldUseOpenAILogo = (model?: Partial<Model> & Record<string, any>) => {
	const modelId = (model?.id ?? model?.name ?? '').toLowerCase();
	return model?.owned_by === 'openai' || modelId === 'gpt-5.5';
};

export const getModelAvatarSrc = (model?: Partial<Model> & Record<string, any>, lang = 'en') => {
	if (shouldUseOpenAILogo(model)) {
		return `${WEBUI_BASE_URL}/openai-mark.svg`;
	}

	const modelId = model?.id ?? model?.value ?? '';
	return `${WEBUI_API_BASE_URL}/models/model/profile/image?id=${modelId}&lang=${lang}`;
};
