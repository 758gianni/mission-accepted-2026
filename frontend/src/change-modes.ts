import data from '../../shared/change-modes.json';
import type { ClearingType } from './types';

export interface ChangeMode {
	id: string;
	label: string;
	overlay: string;
	color: string;
	summary: string;
	signal: string;
	description: string;
	beforeLabel: string;
	afterLabel: string;
	points: number[];
	suggestions: string[];
	events: ClearingType[];
}

export const changeModes: ChangeMode[] = data;
