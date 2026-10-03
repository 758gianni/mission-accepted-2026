import { changeModes } from './change-modes';

// Compatibility export for consumers of the original deforestation view.
const clearings = changeModes[0].events;

export { clearings };
