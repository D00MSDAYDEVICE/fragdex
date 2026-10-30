"""Generate fragdex/importers/huffman_table.py.

Quake 3 / Quake Live network messages are Huffman coded with a tree built by
feeding a fixed frequency table (msg_hData) through an *adaptive* Huffman
algorithm.  The resulting tree is static, so we build it once here with a
faithful port of ioquake3's code/qcommon/huffman.c and emit the code for each
byte.  Takes a minute or so; the output is committed.

Frequency table and algorithm: ioquake3 (GPLv2), code/qcommon/msg.c + huffman.c
"""
import os
import sys

sys.setrecursionlimit(10000)

NYT = 256
INTERNAL_NODE = 257

MSG_HDATA = [
    250315, 41193, 6292, 7106, 3730, 3750, 6110, 23283, 33317, 6950, 7838, 9714, 9257, 17259, 3949, 1778,
    8288, 1604, 1590, 1663, 1100, 1213, 1238, 1134, 1749, 1059, 1246, 1149, 1273, 4486, 2805, 3472,
    21819, 1159, 1670, 1066, 1043, 1012, 1053, 1070, 1726, 888, 1180, 850, 960, 780, 1752, 3296,
    10630, 4514, 5881, 2685, 4650, 3837, 2093, 1867, 2584, 1949, 1972, 940, 1134, 1788, 1670, 1206,
    5719, 6128, 7222, 6654, 3710, 3795, 1492, 1524, 2215, 1140, 1355, 971, 2180, 1248, 1328, 1195,
    1770, 1078, 1264, 1266, 1168, 965, 1155, 1186, 1347, 1228, 1529, 1600, 2617, 2048, 2546, 3275,
    2410, 3585, 2504, 2800, 2675, 6146, 3663, 2840, 14253, 3164, 2221, 1687, 3208, 2739, 3512, 4796,
    4091, 3515, 5288, 4016, 7937, 6031, 5360, 3924, 4892, 3743, 4566, 4807, 5852, 6400, 6225, 8291,
    23243, 7838, 7073, 8935, 5437, 4483, 3641, 5256, 5312, 5328, 5370, 3492, 2458, 1694, 1821, 2121,
    1916, 1149, 1516, 1367, 1236, 1029, 1258, 1104, 1245, 1006, 1149, 1025, 1241, 952, 1287, 997,
    1713, 1009, 1187, 879, 1099, 929, 1078, 951, 1656, 930, 1153, 1030, 1262, 1062, 1214, 1060,
    1621, 930, 1106, 912, 1034, 892, 1158, 990, 1175, 850, 1121, 903, 1087, 920, 1144, 1056,
    3462, 2240, 4397, 12136, 7758, 1345, 1307, 3278, 1950, 886, 1023, 1112, 1077, 1042, 1061, 1071,
    1484, 1001, 1096, 915, 1052, 995, 1070, 876, 1111, 851, 1059, 805, 1112, 923, 1103, 817,
    1899, 1872, 976, 841, 1127, 956, 1159, 950, 7791, 954, 1289, 933, 1127, 3207, 1020, 927,
    1355, 768, 1040, 745, 952, 805, 1073, 740, 1013, 805, 1008, 796, 996, 1057, 11457, 13504,
]
assert len(MSG_HDATA) == 256


class Node:
    __slots__ = ("left", "right", "parent", "next", "prev", "head", "weight", "symbol")

    def __init__(self):
        self.left = self.right = self.parent = self.next = self.prev = self.head = None
        self.weight = 0
        self.symbol = 0


class Slot:
    """Stands in for a node_t** cell; .v holds a Node, or a Slot while on the freelist."""
    __slots__ = ("v",)

    def __init__(self):
        self.v = None


class Huff:
    def __init__(self):
        nyt = Node()
        nyt.symbol = NYT
        self.tree = self.lhead = nyt
        self.loc = [None] * (NYT + 1)
        self.loc[NYT] = nyt
        self.freelist = None

    def get_ppnode(self):
        if self.freelist is None:
            return Slot()
        t = self.freelist
        self.freelist = t.v
        return t

    def free_ppnode(self, slot):
        slot.v = self.freelist
        self.freelist = slot

    def swap(self, node1, node2):
        par1, par2 = node1.parent, node2.parent
        if par1 is not None:
            if par1.left is node1:
                par1.left = node2
            else:
                par1.right = node2
        else:
            self.tree = node2
        if par2 is not None:
            if par2.left is node2:
                par2.left = node1
            else:
                par2.right = node1
        else:
            self.tree = node1
        node1.parent = par2
        node2.parent = par1

    @staticmethod
    def swaplist(node1, node2):
        par1 = node1.next
        node1.next = node2.next
        node2.next = par1
        par1 = node1.prev
        node1.prev = node2.prev
        node2.prev = par1
        if node1.next is node1:
            node1.next = node2
        if node2.next is node2:
            node2.next = node1
        if node1.next is not None:
            node1.next.prev = node1
        if node2.next is not None:
            node2.next.prev = node2
        if node1.prev is not None:
            node1.prev.next = node1
        if node2.prev is not None:
            node2.prev.next = node2

    def increment(self, node):
        while node is not None:
            if node.next is not None and node.next.weight == node.weight:
                lnode = node.head.v
                if lnode is not node.parent:
                    self.swap(lnode, node)
                self.swaplist(lnode, node)
            if node.prev is not None and node.prev.weight == node.weight:
                node.head.v = node.prev
            else:
                node.head.v = None
                self.free_ppnode(node.head)
            node.weight += 1
            if node.next is not None and node.next.weight == node.weight:
                node.head = node.next.head
            else:
                node.head = self.get_ppnode()
                node.head.v = node
            if node.parent is None:
                return
            # C recurses into the parent and then does the fix-up below; the
            # fix-up only touches node/parent list links, so recurse explicitly.
            self.increment(node.parent)
            if node.prev is node.parent:
                self.swaplist(node, node.parent)
                if node.head.v is node:
                    node.head.v = node.parent
            return

    def add_ref(self, ch):
        if self.loc[ch] is None:
            tnode, tnode2 = Node(), Node()
            lhead = self.lhead

            tnode2.symbol = INTERNAL_NODE
            tnode2.weight = 1
            tnode2.next = lhead.next
            if lhead.next is not None:
                lhead.next.prev = tnode2
                if lhead.next.weight == 1:
                    tnode2.head = lhead.next.head
                else:
                    tnode2.head = self.get_ppnode()
                    tnode2.head.v = tnode2
            else:
                tnode2.head = self.get_ppnode()
                tnode2.head.v = tnode2
            lhead.next = tnode2
            tnode2.prev = lhead

            tnode.symbol = ch
            tnode.weight = 1
            tnode.next = lhead.next
            if lhead.next is not None:
                lhead.next.prev = tnode
                if lhead.next.weight == 1:
                    tnode.head = lhead.next.head
                else:
                    tnode.head = self.get_ppnode()
                    tnode.head.v = tnode2
            else:
                tnode.head = self.get_ppnode()
                tnode.head.v = tnode
            lhead.next = tnode
            tnode.prev = lhead
            tnode.left = tnode.right = None

            if lhead.parent is not None:
                if lhead.parent.left is lhead:
                    lhead.parent.left = tnode2
                else:
                    lhead.parent.right = tnode2
            else:
                self.tree = tnode2

            tnode2.right = tnode
            tnode2.left = lhead
            tnode2.parent = lhead.parent
            lhead.parent = tnode.parent = tnode2

            self.loc[ch] = tnode
            self.increment(tnode2.parent)
        else:
            self.increment(self.loc[ch])


def build_codes():
    h = Huff()
    for sym, count in enumerate(MSG_HDATA):
        for _ in range(count):
            h.add_ref(sym)
    codes = {}

    def walk(node, path):
        if node.symbol == INTERNAL_NODE:
            walk(node.left, path + "0")
            walk(node.right, path + "1")
        else:
            codes[node.symbol] = path

    walk(h.tree, "")
    return codes


def main():
    codes = build_codes()
    missing = [s for s in range(256) if s not in codes]
    assert not missing, missing
    out = os.path.join(os.path.dirname(__file__), "..", "fragdex", "importers", "huffman_table.py")
    with open(out, "w", encoding="utf-8") as f:
        f.write('"""Generated by tools/gen_huffman.py - do not edit.\n\n')
        f.write("Bit strings in stream order (first char = first bit read) for each byte\n")
        f.write('value of the Quake 3 / Quake Live network message Huffman tree."""\n\n')
        f.write("CODES = {\n")
        for s in range(256):
            f.write(f"    {s}: {codes[s]!r},\n")
        f.write(f"    256: {codes.get(NYT, '')!r},  # NYT\n")
        f.write("}\n")
    lens = [len(codes[s]) for s in range(256)]
    print("wrote", os.path.abspath(out), "min/max code length", min(lens), max(lens))


if __name__ == "__main__":
    main()
