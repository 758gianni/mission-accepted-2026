import { Minus, Plus } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type FC, type MouseEvent as ReactMouseEvent, type PointerEvent, type WheelEvent } from 'react';
import { clusterCandidates, geometryCenter, geometryPath, getBasemapTiles, projectMapCoordinate, temporalClassOf, type AnalysisTile, type CandidateFeature, type DashboardContract, type GeoBounds } from '../terra-data';
import type { MapLayer } from '../app';

export const MAP_VIEW_WIDTH = 1400;
export const MAP_VIEW_HEIGHT = 900;

export interface MapView { zoom: number; x: number; y: number }

interface ChangeMapProps {
	contract: DashboardContract;
	features: CandidateFeature[];
	selectedId: string | number | null;
	layer: MapLayer;
	view: MapView;
	showFootprints: boolean;
	onSelect: (feature: CandidateFeature) => void;
	onView: (view: MapView) => void;
}

const assetUrl = (value?: string | null) => value ? `/data/${value.replace(/^\//, '')}` : undefined;
const fallbackBounds: GeoBounds = { west: -180, south: -80, east: 180, north: 80 };
const roundPixel = (value: number) => Math.round(value * 100) / 100;

function tileTransform(tile: AnalysisTile, bounds: GeoBounds): string {
	const northWest = projectMapCoordinate([tile.display_bounds.west, tile.display_bounds.north], bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT);
	const southEast = projectMapCoordinate([tile.display_bounds.east, tile.display_bounds.south], bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT);
	const a = (southEast[0] - northWest[0]) / (tile.preview_width ?? tile.width);
	const d = (southEast[1] - northWest[1]) / (tile.preview_height ?? tile.height);
	return `matrix(${roundPixel(a)} 0 0 ${roundPixel(d)} ${roundPixel(northWest[0])} ${roundPixel(northWest[1])})`;
}

function basemapTransform(tileBounds: GeoBounds, bounds: GeoBounds): string {
	const northWest = projectMapCoordinate([tileBounds.west, tileBounds.north], bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT);
	const southEast = projectMapCoordinate([tileBounds.east, tileBounds.south], bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT);
	return `matrix(${(southEast[0] - northWest[0]) / 256} 0 0 ${(southEast[1] - northWest[1]) / 256} ${northWest[0]} ${northWest[1]})`;
}

interface DragState { pointerId: number; startView: MapView; startX: number; startY: number; moved: boolean }

const ChangeMap: FC<ChangeMapProps> = ({ contract, features, selectedId, layer, view, showFootprints, onSelect, onView }) => {
	const bounds = contract.map_bounds ?? contract.scene?.footprint ?? fallbackBounds;
	const imageBounds = contract.image_bounds ?? contract.scene?.footprint ?? bounds;
	const [displayView, setDisplayView] = useState(view);
	const displayViewRef = useRef(view);
	const viewCallbackRef = useRef(onView);
	const svgRef = useRef<SVGSVGElement>(null);
	const [screenScale, setScreenScale] = useState(1);
	useEffect(() => {
		const svg = svgRef.current;
		if (!svg) return;
		const observer = new ResizeObserver(() => setScreenScale(svg.getScreenCTM()?.a ?? 1));
		observer.observe(svg);
		return () => observer.disconnect();
	}, []);
	const dragRef = useRef<DragState | null>(null);
	const [isDragging, setIsDragging] = useState(false);
	const [showStreetDetail, setShowStreetDetail] = useState(false);
	const [contextFeatures, setContextFeatures] = useState<CandidateFeature[]>([]);
	useEffect(() => {
		if (!contract.geographic_context?.asset) return;
		let active = true;
		fetch(`/data/${contract.geographic_context.asset}`).then((response) => response.json()).then((data) => { if (active) setContextFeatures(data.features); }).catch(() => {});
		return () => { active = false; };
	}, [contract.geographic_context?.asset]);
	const [aggregateDetections, setAggregateDetections] = useState(true);
	const selected = features.find((feature) => String(feature.properties.id) === String(selectedId));
	const tileProducts = contract.tiles ?? [];
	const basemapTiles = useMemo(() => getBasemapTiles(bounds, displayView, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT),
		[bounds, displayView]);
	const imagePath = assetUrl(contract.imagery?.[layer]);
	const imageLayout = contract.imagery_layout?.[layer];
	const clusters = useMemo(() => clusterCandidates(features, bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT, 44 / Math.max(view.zoom, 0.5), projectMapCoordinate),
		[features, bounds, view.zoom]);
	const candidatePoints = useMemo(() => features.map((feature) => ({
		feature,
		center: geometryCenter(feature.geometry, bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT, projectMapCoordinate),
	})), [features, bounds]);
	const visiblePoints = useMemo(() => {
		const halfWidth = MAP_VIEW_WIDTH / (2 * displayView.zoom) * 1.3;
		const halfHeight = MAP_VIEW_HEIGHT / (2 * displayView.zoom) * 1.3;
		return candidatePoints.filter(({ center: [x, y] }) => Math.abs(x - displayView.x) <= halfWidth && Math.abs(y - displayView.y) <= halfHeight);
	}, [candidatePoints, displayView.x, displayView.y, displayView.zoom]);
	const regionalView = displayView.zoom < 3;
	const detailOpacity = Math.max(0, Math.min(1, (displayView.zoom - 5) / 5));
	const coverageOpacity = 1 - detailOpacity;
	const maximumObservationDates = Math.max(1, ...tileProducts.map((tile) => tile.dates.length));
	const drawPolygons = displayView.zoom >= 10 && visiblePoints.length <= 1400;
	const translateX = MAP_VIEW_WIDTH / 2 - displayView.x * displayView.zoom;
	const translateY = MAP_VIEW_HEIGHT / 2 - displayView.y * displayView.zoom;
	const projectionScale = Math.min(MAP_VIEW_WIDTH / ((bounds.east - bounds.west) * Math.cos((bounds.north + bounds.south) * Math.PI / 360)), MAP_VIEW_HEIGHT / (bounds.north - bounds.south));
	const scaleDistanceKm = 48 * 111.32 / (projectionScale * screenScale * displayView.zoom);
	const scaleLabel = scaleDistanceKm < 1 ? `${Math.round(scaleDistanceKm * 1000 / 10) * 10} m` : `${scaleDistanceKm.toLocaleString('en', { maximumFractionDigits: 1 })} km`;

	useEffect(() => { viewCallbackRef.current = onView; }, [onView]);

	useEffect(() => {
		const handleMove = (event: globalThis.PointerEvent) => {
			const drag = dragRef.current;
			const svg = svgRef.current;
			if (!drag || !svg || event.pointerId !== drag.pointerId) return;
			const matrix = svg.getScreenCTM();
			if (!matrix) return;
			const point = svg.createSVGPoint();
			point.x = event.clientX;
			point.y = event.clientY;
			const current = point.matrixTransform(matrix.inverse());
			if (!drag.moved && Math.hypot(current.x - drag.startX, current.y - drag.startY) < 3) return;
			drag.moved = true;
			setIsDragging(true);
			const next = {
				zoom: drag.startView.zoom,
				x: drag.startView.x - (current.x - drag.startX) / drag.startView.zoom,
				y: drag.startView.y - (current.y - drag.startY) / drag.startView.zoom,
			};
			displayViewRef.current = next;
			setDisplayView(next);
			viewCallbackRef.current(next);
		};
		const handleUp = (event: globalThis.PointerEvent) => {
			if (dragRef.current?.pointerId !== event.pointerId) return;
			dragRef.current = null;
			setIsDragging(false);
		};
		window.addEventListener('pointermove', handleMove);
		window.addEventListener('pointerup', handleUp);
		window.addEventListener('pointercancel', handleUp);
		return () => {
			window.removeEventListener('pointermove', handleMove);
			window.removeEventListener('pointerup', handleUp);
			window.removeEventListener('pointercancel', handleUp);
		};
	}, []);

	useEffect(() => {
		if (dragRef.current?.moved) {
			displayViewRef.current = view;
			return;
		}
		let frame = 0;
		let started = 0;
		const from = displayViewRef.current;
		if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
			displayViewRef.current = view;
			frame = requestAnimationFrame(() => setDisplayView(view));
			return () => cancelAnimationFrame(frame);
		}
		const animate = (now: number) => {
			if (!started) started = now;
			const raw = Math.min(1, (now - started) / 520);
			const progress = 1 - (1 - raw) ** 3;
			const next = {
				zoom: from.zoom + (view.zoom - from.zoom) * progress,
				x: from.x + (view.x - from.x) * progress,
				y: from.y + (view.y - from.y) * progress,
			};
			displayViewRef.current = next;
			setDisplayView(next);
			if (raw < 1) frame = requestAnimationFrame(animate);
		};
		frame = requestAnimationFrame(animate);
		return () => cancelAnimationFrame(frame);
	}, [view]);

	const pointAt = (event: PointerEvent<SVGSVGElement>) => {
		const matrix = event.currentTarget.getScreenCTM();
		if (!matrix) return null;
		const point = event.currentTarget.createSVGPoint();
		point.x = event.clientX;
		point.y = event.clientY;
		return point.matrixTransform(matrix.inverse());
	};
	const handlePointerDown = (event: PointerEvent<SVGSVGElement>) => {
		if (event.button !== 0) return;
		const point = pointAt(event);
		if (!point) return;
		dragRef.current = { pointerId: event.pointerId, startView: displayViewRef.current, startX: point.x, startY: point.y, moved: false };
	};
	const adjustZoom = (amount: number) => onView({ ...view, zoom: Math.max(0.6, Math.min(128, view.zoom + amount)) });
	const handleWheel = (event: WheelEvent<SVGSVGElement>) => {
		event.preventDefault();
		adjustZoom(event.deltaY < 0 ? 0.3 : -0.3);
	};
	const handleDoubleClick = (event: ReactMouseEvent<SVGSVGElement>) => {
		event.preventDefault();
		adjustZoom(0.8);
	};
	const focusCluster = (cluster: { x: number; y: number; count: number }) => onView({
		zoom: Math.min(128, Math.max(view.zoom + 1.2, cluster.count > 30 ? 2.8 : 3.4)), x: cluster.x, y: cluster.y,
	});
	const focusFeature = (feature: CandidateFeature) => {
		const [x, y] = geometryCenter(feature.geometry, bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT, projectMapCoordinate);
		onView({ zoom: Math.max(view.zoom, 4.1), x, y });
		onSelect(feature);
	};
	const temporalColor = (feature: CandidateFeature) => {
		const temporal = temporalClassOf(feature.properties);
		return temporal === 't4_validated_persistent' ? '#f4ce78' : temporal === 'persistent' ? '#f06a87' : temporal === 'seasonal_transient' ? '#64d4d2' : '#bbc5c0';
	};
	const footprintColor = (index: number) => ['#d1e4d8', '#6ad4d2', '#ff9c79', '#f4ce78'][index % 4];
	const imageCorners: [number, number][] = [
		[imageBounds.west, imageBounds.north], [imageBounds.east, imageBounds.north],
		[imageBounds.east, imageBounds.south], [imageBounds.west, imageBounds.south],
	];
	const staticImageTile: AnalysisTile = {
		tile_id: 'overview-image', crs: 'EPSG:4326', corners: imageCorners,
		width: MAP_VIEW_WIDTH, height: MAP_VIEW_HEIGHT, dates: [], source_record_ids: [],
		display_bounds: imageBounds,
		analysis_valid_pixels: 0, satellite_coverage_pixels: 0, insufficient_evidence_pixels: 0, layers: {},
	};

	return (
		<div className='ts-map-canvas'>
			<svg ref={svgRef} data-testid='map-viewport' data-map-center={`${roundPixel(displayView.x)},${roundPixel(displayView.y)}`}
				data-map-zoom={roundPixel(displayView.zoom)} data-map-level={regionalView ? 'regional' : detailOpacity > 0 ? 'investigation' : 'discovery'} className={`ts-map-svg ${isDragging ? 'is-dragging' : ''}`}
				viewBox={`0 0 ${MAP_VIEW_WIDTH} ${MAP_VIEW_HEIGHT}`} preserveAspectRatio='xMidYMid meet' overflow='visible' role='application'
				aria-label={`${contract.scene?.name ?? 'Area of interest'} anomaly distribution. ${features.length} candidates shown.`}
				onWheel={handleWheel} onDoubleClick={handleDoubleClick} onPointerDown={handlePointerDown}>
				<defs>
					<linearGradient id='mapFade' x1='0' x2='1' y1='0' y2='1'><stop stopColor='#171d22' /><stop offset='1' stopColor='#222b31' /></linearGradient>
					<filter id='softHalo' x='-100%' y='-100%' width='300%' height='300%'><feGaussianBlur stdDeviation='7' /></filter>
					<clipPath id='sceneClip'><rect x='0' y='0' width={MAP_VIEW_WIDTH} height={MAP_VIEW_HEIGHT} rx='10' /></clipPath>
				</defs>
				<rect width={MAP_VIEW_WIDTH} height={MAP_VIEW_HEIGHT} fill='url(#mapFade)' />
				<g><g transform={`translate(${translateX} ${translateY}) scale(${displayView.zoom})`}>
					{contextFeatures.map((feature, index) => <path key={`context-${index}`} className={feature.properties.kind === 'land' ? 'ts-context-land' : 'ts-context-river'} d={geometryPath(feature.geometry, bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT, projectMapCoordinate)} strokeWidth={(feature.properties.kind === 'land' ? 1 : .7) / displayView.zoom} pointerEvents='none' />)}
					{contextFeatures.filter((feature) => feature.properties.kind === 'land' && Array.isArray(feature.properties.label)).map((feature, index) => { const [x, y] = projectMapCoordinate(feature.properties.label as number[], bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT); return <text key={`label-${index}`} className='ts-context-label' x={x} y={y} textAnchor='middle' fontSize={13 / displayView.zoom} pointerEvents='none'>{String(feature.properties.name).toUpperCase()}</text>; })}
					{showStreetDetail && basemapTiles.map((tile) => <g className='ts-basemap-tile' key={`${tile.z}/${tile.x}/${tile.y}`} data-basemap-tile={`${tile.z}/${tile.x}/${tile.y}`} transform={basemapTransform(tile.bounds, bounds)} pointerEvents='none'>
						<image href={tile.src} x='0' y='0' width='256' height='256' preserveAspectRatio='none' pointerEvents='none' onLoad={(event) => event.currentTarget.setAttribute('data-loaded', 'true')} />
					</g>)}
					{coverageOpacity > 0 && <g className='ts-coverage-surface' opacity={coverageOpacity} pointerEvents='none'>{tileProducts.map((tile) => {
						const geometry = { type: 'Polygon', coordinates: [[...tile.corners, tile.corners[0]]] };
						return <path key={tile.tile_id} className={`ts-coverage-footprint ${tile.analysis_valid_pixels ? 'is-analyzed' : 'is-sparse'}`} d={geometryPath(geometry, bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT, projectMapCoordinate)} fillOpacity={.1 + .2 * Math.min(1, tile.dates.length / maximumObservationDates)} strokeWidth={.8 / displayView.zoom}><title>{tile.tile_id} · {tile.dates.length} observation dates · {tile.source_record_ids.length} source acquisitions</title></path>;
					})}</g>}
					{detailOpacity > 0 && <g className='ts-sar-detail' opacity={detailOpacity}>
					{imagePath && <g className='ts-overview-layer' transform={tileTransform(staticImageTile, bounds)}>{imageLayout ? <svg width={MAP_VIEW_WIDTH} height={MAP_VIEW_HEIGHT} viewBox={`${imageLayout.x} ${imageLayout.y} ${imageLayout.content_width} ${imageLayout.content_height}`} preserveAspectRatio='none' overflow='hidden'><image href={imagePath} width={imageLayout.width} height={imageLayout.height} /></svg> : <image href={imagePath} x='0' y='0' width={MAP_VIEW_WIDTH} height={MAP_VIEW_HEIGHT} preserveAspectRatio='none' />}</g>}
					{tileProducts.map((tile) => {
						const asset = assetUrl(tile.layers[layer]);
						if (!asset) return null;
						return <g className='ts-map-tile' key={tile.tile_id} data-tile-id={tile.tile_id} data-geographic-corners={JSON.stringify(tile.corners)} data-display-bounds={JSON.stringify(tile.display_bounds)} transform={tileTransform(tile, bounds)}>
							<image href={asset} x='0' y='0' width={tile.preview_width ?? tile.width} height={tile.preview_height ?? tile.height} preserveAspectRatio='none' />
						</g>;
					})}
					</g>}
					{showFootprints && (contract.acquisitions ?? []).map((acquisition, index) => {
						const path = geometryPath(acquisition.footprint, bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT, projectMapCoordinate);
						return path ? <path key={acquisition.id} d={path} fill='none' stroke={footprintColor(index)} strokeWidth={2 / displayView.zoom} strokeDasharray={`${7 / displayView.zoom} ${4 / displayView.zoom}`} opacity={regionalView ? .3 : .8} pointerEvents='none' /> : null;
					})}
					{(regionalView || aggregateDetections) && displayView.zoom < 5 ? clusters.map((cluster, index) => {
						const color = cluster.validatedCount ? '#f4ce78' : cluster.persistentCount ? '#f06a87' : '#64d4d2';
						const radius = (11 + Math.min(10, Math.log2(cluster.count + 1) * 3)) / displayView.zoom;
						return <g key={`${Math.round(cluster.x)}-${Math.round(cluster.y)}-${index}`} role='button' tabIndex={0}
							aria-label={`Zoom to cluster of ${cluster.count} anomalies`} className='ts-cluster-marker'
							data-candidate-ids={JSON.stringify(cluster.features.map((feature) => String(feature.properties.id)))}
							onClick={() => focusCluster(cluster)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') focusCluster(cluster); }}>
							<title>{`${cluster.count} detections · ${cluster.persistentCount} persistent · ${cluster.validatedCount} independently supported`}</title>
							<circle cx={cluster.x} cy={cluster.y} r={radius * 1.4} fill='none' stroke={color} strokeOpacity='.18' strokeWidth={1 / displayView.zoom} />
							<circle cx={cluster.x} cy={cluster.y} r={radius} fill='#202830' fillOpacity='.95' stroke={color} strokeOpacity='.8' strokeWidth={1.4 / displayView.zoom} />
							<text x={cluster.x} y={cluster.y + 4 / displayView.zoom} textAnchor='middle' fontSize={Math.min(radius * 1.1, 14 / displayView.zoom)} fill={color} fontWeight='600'>{cluster.count}</text>
						</g>;
					}) : drawPolygons ? visiblePoints.map(({ feature }) => {
						const d = geometryPath(feature.geometry, bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT, projectMapCoordinate);
						const isSelected = String(feature.properties.id) === String(selectedId);
						const color = temporalColor(feature);
						return <path key={String(feature.properties.id)} d={d} fill={color} fillOpacity={isSelected ? .25 : selected ? .04 : .12}
							stroke={isSelected ? '#f4d17f' : color} strokeWidth={(isSelected ? 3.2 : 1.15) / displayView.zoom} strokeLinejoin='round'
							className={`ts-candidate-shape ${isSelected ? 'is-selected' : ''}`} data-candidate-id={String(feature.properties.id)} role='button' tabIndex={0} aria-label={`Select region ${feature.properties.id}`}
							onClick={() => focusFeature(feature)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') focusFeature(feature); }} />;
					}) : visiblePoints.map(({ feature, center: [x, y] }) => {
						const isSelected = String(feature.properties.id) === String(selectedId);
						return <circle key={String(feature.properties.id)} cx={x} cy={y} r={(isSelected ? 5 : 3) / displayView.zoom}
							fill={isSelected ? '#f4d17f' : temporalColor(feature)} fillOpacity={isSelected ? 1 : 0.82}
							stroke='#111a16' strokeWidth={0.7 / displayView.zoom} className='ts-candidate-point' data-candidate-id={String(feature.properties.id)}
							role='button' tabIndex={0} aria-label={`Select region ${feature.properties.id}`}
							onClick={() => focusFeature(feature)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') focusFeature(feature); }} />;
					})}
					{selected && (regionalView || aggregateDetections) && displayView.zoom < 5 && (() => {
						const d = geometryPath(selected.geometry, bounds, MAP_VIEW_WIDTH, MAP_VIEW_HEIGHT, projectMapCoordinate);
						return <path d={d} fill='#f4d17f' fillOpacity='.55' stroke='#fff2ca' strokeWidth={3 / displayView.zoom} pointerEvents='none' />;
					})()}
				</g></g>
			</svg>
			<button type='button' className='ts-detection-mode' aria-pressed={aggregateDetections} disabled={regionalView || displayView.zoom >= 5} onClick={() => setAggregateDetections((current) => !current)}>{drawPolygons ? 'Candidate geometry' : displayView.zoom >= 5 ? 'Individual detections' : regionalView || aggregateDetections ? 'Clustered detections' : 'Individual detections'}<span>{displayView.zoom >= 5 ? 'Select a signal to inspect evidence' : regionalView ? 'Zoom to resolve individual signals' : aggregateDetections ? 'Show individual points' : 'Group nearby points'}</span></button>
			<div className='ts-zoom-control' aria-label='Map zoom controls'>
				<button type='button' aria-label='Zoom in' onClick={() => adjustZoom(0.45)}><Plus size={16} /></button>
				<span>{displayView.zoom.toFixed(1)}×</span>
				<button type='button' aria-label='Zoom out' onClick={() => adjustZoom(-0.45)}><Minus size={16} /></button>
			</div>
			<button type='button' className='ts-basemap-mode' aria-pressed={showStreetDetail} onClick={() => setShowStreetDetail((current) => !current)}>Street detail <span>{showStreetDetail ? 'On' : 'Off'}</span></button>
			{coverageOpacity > 0 && <div className='ts-coverage-key' style={{ opacity: coverageOpacity }}><span />Tile coverage · brightness reflects observation dates</div>}
			<div className='ts-map-scale'><span />{scaleLabel} <small>approx.</small></div>
			<div className='ts-map-coordinate'>WGS 84 · {bounds.west.toFixed(2)}°E — {bounds.east.toFixed(2)}°E</div>
		</div>
	);
};

export { ChangeMap };
