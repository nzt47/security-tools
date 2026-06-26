#!/usr/bin/env python3
"""
用户反馈 API 路由模块

提供用户反馈的 REST API 接口：
- POST /api/feedback/submit - 提交用户反馈
- GET /api/feedback/list - 获取反馈列表
- GET /api/feedback/summary - 获取反馈汇总统计
- GET /api/feedback/<feedback_id> - 获取单个反馈详情
- POST /api/feedback/<feedback_id>/resolve - 标记反馈为已解决
- GET /api/feedback/quality-cases - 获取优质案例列表
- GET /api/feedback/report - 生成反馈分析报告
"""

import json
from .tracing_decorator import trace_route
import logging
from flask import request, jsonify

logger = logging.getLogger(__name__)


def register_routes(app, state):
    """注册用户反馈相关路由

    Args:
        app: Flask 应用实例
        state: 全局状态容器
    """

    @app.route('/api/feedback/submit', methods=['POST'])
    @trace_route("Feedback")
    def submit_feedback():
        """提交用户反馈

        Request Body:
            trace_id: 关联的追踪ID（必填）
            feedback_type: 反馈类型 - like/dislike/report/suggestion（必填）
            rating: 评分 1-5（可选）
            comment: 反馈评论（可选）
            category: 反馈分类（可选）
            user_id: 用户ID（可选）
            session_id: 会话ID（可选）
            context: 上下文信息（可选）
        """
        try:
            data = request.get_json() or {}

            trace_id = data.get('trace_id', '')
            feedback_type = data.get('feedback_type', '')
            rating = int(data.get('rating', 0))
            comment = data.get('comment', '')
            category = data.get('category', 'other')
            user_id = data.get('user_id', '')
            session_id = data.get('session_id', '')
            context = data.get('context', {})

            if not trace_id:
                return jsonify({
                    "success": False,
                    "error": "trace_id 不能为空",
                    "code": "MISSING_TRACE_ID"
                }), 400

            if not feedback_type:
                return jsonify({
                    "success": False,
                    "error": "feedback_type 不能为空",
                    "code": "MISSING_FEEDBACK_TYPE"
                }), 400

            valid_types = ['like', 'dislike', 'report', 'suggestion']
            if feedback_type not in valid_types:
                return jsonify({
                    "success": False,
                    "error": f"无效的 feedback_type，有效值: {valid_types}",
                    "code": "INVALID_FEEDBACK_TYPE"
                }), 400

            from agent.feedback import get_feedback_manager
            manager = get_feedback_manager()

            record = manager.submit_feedback(
                trace_id=trace_id,
                feedback_type=feedback_type,
                rating=rating,
                comment=comment,
                category=category,
                user_id=user_id,
                session_id=session_id,
                context=context
            )

            logger.info(json.dumps({
                "trace_id": trace_id,
                "module_name": "routes_feedback",
                "action": "submit_feedback",
                "feedback_id": record.feedback_id,
                "feedback_type": feedback_type,
                "duration_ms": 0,
                "level": "INFO"
            }))

            return jsonify({
                "success": True,
                "data": record.to_dict(),
                "message": "反馈提交成功"
            }), 200

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "routes_feedback",
                "action": "submit_feedback",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            return jsonify({
                "success": False,
                "error": str(e),
                "code": "INTERNAL_ERROR"
            }), 500

    @app.route('/api/feedback/list', methods=['GET'])
    @trace_route("Feedback")
    def list_feedback():
        """获取反馈列表

        Query Parameters:
            feedback_type: 反馈类型过滤（可选）
            status: 状态过滤（可选）
            category: 分类过滤（可选）
            user_id: 用户ID过滤（可选）
            trace_id: trace_id过滤（可选）
            limit: 数量限制（默认50）
            offset: 偏移量（默认0）
        """
        try:
            feedback_type = request.args.get('feedback_type')
            status = request.args.get('status')
            category = request.args.get('category')
            user_id = request.args.get('user_id', '')
            trace_id = request.args.get('trace_id', '')
            limit = int(request.args.get('limit', 50))
            offset = int(request.args.get('offset', 0))

            from agent.feedback import get_feedback_manager
            manager = get_feedback_manager()

            records = manager.list_feedback(
                feedback_type=feedback_type,
                status=status,
                category=category,
                user_id=user_id,
                trace_id=trace_id,
                limit=limit,
                offset=offset
            )

            return jsonify({
                "success": True,
                "data": [r.to_dict() for r in records],
                "total": len(records),
                "limit": limit,
                "offset": offset
            }), 200

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "routes_feedback",
                "action": "list_feedback",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            return jsonify({
                "success": False,
                "error": str(e),
                "code": "INTERNAL_ERROR"
            }), 500

    @app.route('/api/feedback/<feedback_id>', methods=['GET'])
    def get_feedback(feedback_id):
        """获取单个反馈详情"""
        try:
            from agent.feedback import get_feedback_manager
            manager = get_feedback_manager()

            record = manager.get_feedback(feedback_id)
            if not record:
                return jsonify({
                    "success": False,
                    "error": "反馈不存在",
                    "code": "NOT_FOUND"
                }), 404

            return jsonify({
                "success": True,
                "data": record.to_dict()
            }), 200

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "routes_feedback",
                "action": "get_feedback",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            return jsonify({
                "success": False,
                "error": str(e),
                "code": "INTERNAL_ERROR"
            }), 500

    @app.route('/api/feedback/<feedback_id>/resolve', methods=['POST'])
    @trace_route("Feedback")
    def resolve_feedback(feedback_id):
        """标记反馈为已解决

        Request Body:
            resolution: 解决方案说明（可选）
            resolver: 处理人（可选）
        """
        try:
            data = request.get_json() or {}
            resolution = data.get('resolution', '')
            resolver = data.get('resolver', '')

            from agent.feedback import get_feedback_manager
            manager = get_feedback_manager()

            success = manager.resolve_feedback(
                feedback_id=feedback_id,
                resolution=resolution,
                resolver=resolver
            )

            return jsonify({
                "success": success,
                "message": "反馈已标记为已解决" if success else "操作失败"
            }), 200

        except ValueError as e:
            return jsonify({
                "success": False,
                "error": str(e),
                "code": "NOT_FOUND"
            }), 404
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "routes_feedback",
                "action": "resolve_feedback",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            return jsonify({
                "success": False,
                "error": str(e),
                "code": "INTERNAL_ERROR"
            }), 500

    @app.route('/api/feedback/summary', methods=['GET'])
    def feedback_summary():
        """获取反馈汇总统计

        Query Parameters:
            days: 统计天数（默认7）
        """
        try:
            days = int(request.args.get('days', 7))

            from agent.feedback import get_feedback_manager
            manager = get_feedback_manager()

            summary = manager.get_feedback_summary(days=days)

            return jsonify({
                "success": True,
                "data": summary
            }), 200

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "routes_feedback",
                "action": "feedback_summary",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            return jsonify({
                "success": False,
                "error": str(e),
                "code": "INTERNAL_ERROR"
            }), 500

    @app.route('/api/feedback/quality-cases', methods=['GET'])
    @trace_route("Feedback")
    def list_quality_cases():
        """获取优质案例列表

        Query Parameters:
            tags: 标签过滤，逗号分隔（可选）
            limit: 数量限制（默认50）
            offset: 偏移量（默认0）
        """
        try:
            tags_str = request.args.get('tags', '')
            tags = [t.strip() for t in tags_str.split(',') if t.strip()] if tags_str else None
            limit = int(request.args.get('limit', 50))
            offset = int(request.args.get('offset', 0))

            from agent.feedback import get_feedback_manager
            manager = get_feedback_manager()

            cases = manager.list_quality_cases(
                tags=tags,
                limit=limit,
                offset=offset
            )

            return jsonify({
                "success": True,
                "data": [c.to_dict() for c in cases],
                "total": len(cases),
                "limit": limit,
                "offset": offset
            }), 200

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "routes_feedback",
                "action": "list_quality_cases",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            return jsonify({
                "success": False,
                "error": str(e),
                "code": "INTERNAL_ERROR"
            }), 500

    @app.route('/api/feedback/report', methods=['GET'])
    @trace_route("Feedback")
    def feedback_report():
        """生成反馈分析报告

        Query Parameters:
            days: 统计天数（默认7）
        """
        try:
            days = int(request.args.get('days', 7))

            from agent.feedback import get_feedback_manager
            manager = get_feedback_manager()

            report = manager.generate_feedback_report(days=days)

            return jsonify({
                "success": True,
                "data": report
            }), 200

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": "",
                "module_name": "routes_feedback",
                "action": "feedback_report",
                "error": str(e),
                "duration_ms": 0,
                "level": "ERROR"
            }))
            return jsonify({
                "success": False,
                "error": str(e),
                "code": "INTERNAL_ERROR"
            }), 500

    logger.info(json.dumps({
        "trace_id": "",
        "module_name": "routes_feedback",
        "action": "register_routes",
        "message": "用户反馈路由注册完成",
        "duration_ms": 0,
        "level": "INFO"
    }))
