def evaluate_single_image_tp_fp_fn(
    pred_heatmap,
    gt_boxes,
    ratio_w,
    ratio_h,
    offset_x,
    offset_y,
    iou_thresholds,
    param_pairs,
    text_threshold,
    min_pixel,
):
    """Compute tp/fp/fn for one image across thresholds and params.

    Returns:
        dict: {iou_thresh: {param_pair: {'tp': tp, 'fp': fp, 'fn': fn}, ...}, ...}
    """
    results = {iou_thresh: {} for iou_thresh in iou_thresholds}

    # Cache boxes to avoid recomputing the expensive detection step.
    boxes_by_param = {}
    for param_pair in param_pairs:
        low_text, expand_ratio = param_pair
        boxes = get_det_bboxes_fast(
            pred_heatmap, text_threshold, low_text, expand_ratio, min_pixel
        )
        boxes = adjust_result_coordinates_fast(
            boxes, ratio_w, ratio_h, offset_x, offset_y
        )
        boxes_by_param[param_pair] = boxes

    for iou_thresh in iou_thresholds:
        for param_pair in param_pairs:
            boxes = boxes_by_param[param_pair]
            tp, fp, fn = evaluate_quad_detection_fast(
                boxes, gt_boxes, iou_thresh=iou_thresh
            )
            results[iou_thresh][param_pair] = {"tp": tp, "fp": fp, "fn": fn}

    return results
