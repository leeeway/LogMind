import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Form, Input, Button, Typography, message, Card } from 'antd';
import { ThunderboltOutlined, UserOutlined, LockOutlined } from '@ant-design/icons';
import { authApi } from '@/api/auth';
import { useAuthStore } from '@/stores/authStore';

const { Title, Text } = Typography;

const Login: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { setAuth } = useAuthStore();

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const { data } = await authApi.login(values.username, values.password);
      setAuth(data.access_token, data.user);
      message.success('登录成功');
      navigate('/');
    } catch (err: any) {
      message.error(err.response?.data?.detail || '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'linear-gradient(135deg, #0a0e17 0%, #111827 50%, #0d1b2a 100%)',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Background glow */}
      <div style={{
        position: 'absolute',
        width: 500,
        height: 500,
        borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(22,119,255,0.12) 0%, transparent 70%)',
        top: '10%',
        left: '20%',
        filter: 'blur(60px)',
      }} />
      <div style={{
        position: 'absolute',
        width: 400,
        height: 400,
        borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(82,196,26,0.08) 0%, transparent 70%)',
        bottom: '15%',
        right: '15%',
        filter: 'blur(50px)',
      }} />

      <Card
        style={{
          width: 420,
          background: 'rgba(30, 41, 59, 0.8)',
          backdropFilter: 'blur(20px)',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: 16,
          boxShadow: '0 16px 48px rgba(0,0,0,0.5)',
        }}
        styles={{ body: { padding: '40px 36px' } }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <ThunderboltOutlined style={{
            fontSize: 40,
            color: '#1677ff',
            display: 'block',
            marginBottom: 12,
          }} />
          <Title level={3} style={{ margin: 0, color: 'rgba(255,255,255,0.88)' }}>
            LogMind
          </Title>
          <Text style={{ color: 'rgba(255,255,255,0.45)', fontSize: 13 }}>
            AI 智能日志分析平台
          </Text>
        </div>

        <Form layout="vertical" onFinish={onFinish} autoComplete="off" size="large">
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input
              prefix={<UserOutlined style={{ color: 'rgba(255,255,255,0.3)' }} />}
              placeholder="用户名"
            />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password
              prefix={<LockOutlined style={{ color: 'rgba(255,255,255,0.3)' }} />}
              placeholder="密码"
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" loading={loading} block style={{
              height: 44,
              borderRadius: 8,
              fontWeight: 600,
              fontSize: 15,
            }}>
              登 录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
};

export default Login;
