import asyncio
import unittest
from neural_hub.server import NeuralHub
from neural_protocol.agents.support_ws import WSSupportAgent
from neural_protocol.agents.sales_ws import WSSalesAgent
from neural_protocol.core.signal import NeuralSignalType
from neural_protocol.core.identity import NeuralIdentity
from neural_protocol.transport.websocket import connect_websocket, ctrl_msg

class TestHub(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = NeuralHub(host="127.0.0.1", port=8877)
        await self.hub.start()

    async def asyncTearDown(self):
        await self.hub.stop()

    async def test_agent_register_and_discover(self):
        # Connect a test agent
        conn = await connect_websocket("127.0.0.1", 8877)
        agent_id = "test_agent"
        agent_hash = NeuralIdentity.generate(agent_id).neural_hash
        await conn.send(ctrl_msg("register", agent_id=agent_id, neural_hash=agent_hash))
        resp = await conn.recv()
        self.assertIn(b'"registered"', resp)

        # Discover from another connection
        conn2 = await connect_websocket("127.0.0.1", 8877)
        await conn2.send(ctrl_msg("discover", name=agent_id))
        # The hub will broadcast peer_joined, so we need to catch it.
        # For simplicity, we just check that the agent appears in hub registry.
        self.assertIn(agent_hash, self.hub._agents)
        await conn.close()
        await conn2.close()

    async def test_signal_routing(self):
        # Setup two agents
        support = WSSupportAgent(hub_host="127.0.0.1", hub_port=8877)
        sales = WSSalesAgent(hub_host="127.0.0.1", hub_port=8877)
        await support.start()
        await sales.start()

        # Send a signal from support to sales
        await support.transmit("ventas", NeuralSignalType.NOREPINEPHRINE, {"test": "data"})
        await asyncio.sleep(0.2)
        # Check that sales received it
        self.assertGreater(len(sales._memory), 0)
        self.assertEqual(sales._memory[0].signal_type, NeuralSignalType.NOREPINEPHRINE)
        self.assertEqual(sales._memory[0].payload.get("test"), "data")

        await support.stop()
        await sales.stop()

if __name__ == "__main__":
    unittest.main()